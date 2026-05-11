"""
跨光谱匹配与单应性层 (matching layer).

本模块是 9 层管道中的第 4 层，负责在去畸变后的 RGB 与热红外 TIFF 图像之间建立
稳定对应关系，并输出单应性矩阵与质量指标。该层以 TWMM (Template Matching with
Weights + Multilevel Max-Pooling) 为核心算法，不重新实现论文主逻辑，只通过
TwmmAdapter 进行 I/O 转换和流程编排。

主要职责：
1. 加载并初始化 TWMM TAMM_clean 模块（ThermalVisble、CFOG 特征、TAMM 匹配）
2. 处理去畸变双光谱图像对，执行四步匹配流程
   - 原子块相似度图计算
   - 金字塔多尺度相似度图构建
   - 最大索引回溯求精
   - 异常值剔除与单应性估计
3. 输出 MatchResult 合约，包含对应点、质量标记、诊断可视化
4. 支持单对和批量处理，可与后续热富集层无缝衔接

关键设计约束：
- 不依赖 radiometry 层（热数据处理独立进行）
- 所有 TWMM 参数（patch_size、search_radius、scale dict）必须显式来自配置
- 失败时返回低置信结果，保留诊断材料，不伪造高质量输出
- RGB/热红外双光谱特性通过 scale {'thermal': ..., 'visible': ...} 显式表达
"""

from __future__ import annotations

import importlib
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from config.runtime_models import TwmmParams
from pipeline_io.artifact_io import ensure_dir, write_csv_rows
from pipeline_io.json_io import write_json


@dataclass
class MatchResult:
    """
    跨光谱匹配输出合约。记录对应点、质量指标、单应性和诊断信息。
    
    字段语义（遵循 file_formats.md § 8、matching_algorithm.md § 3.2）：
    
    pair_id: 唯一对标识，格式通常为 "DJI_..._0001" 之类
    source_rgb: RGB 源文件路径（绝对或相对于 workspace_root）
    source_thermal: 热红外 TIFF 源文件路径
    
    correspondences: 对应点列表，每个元素包含：
      - rgb_x, rgb_y: RGB 图像中的像素坐标 (float, 左上为原点)
      - thermal_x, thermal_y: 热红外图像中的像素坐标 (float)
      - is_inlier: bool, 是否在最终单应性估计中被标记为内点
    - score: float in [0, 1], 该对应点的匹配置信度（同 matching_algorithm.md / file_formats.md）
    
    inlier_mask: 按 correspondences 顺序的布尔掩码，指示内点
    
    homography_matrix: 3x3 矩阵（行主序转换为 List[List[float]]），
                      将 RGB 平面坐标映射到热红外平面。
                      未标准化；单应性可能退化。
    
    confidence: float in [0, 1]，融合指标，计算公式见 _confidence()。
                - 0.65 × 内点比例 + 0.25 × 点密度项 + 0.10 × 稳定性项
                - 用于快速判断此次匹配的可信度
    
    match_quality: {"high", "medium", "degraded", "low", "low_confidence_failure"}
                  - "high": confidence >= 0.8 且无硬失败
                  - "medium": 0.6 <= confidence < 0.8
                  - "degraded": 0.4 <= confidence < 0.6
                  - "low": confidence < 0.4
                  - "low_confidence_failure": 单应性求解失败、内点过少或条件数过大
    
    runtime_ms: 该对的完整匹配耗时（毫秒）
    
    algorithm_version: TWMM 核心版本标记，如 "TWMM.TAMM_clean.v1"
    
    outlier_ratio: float in [0, 1]，外点占候选对的比例
    inlier_count: 内点数量
    outlier_count: 外点数量
    
    homography_stability: {
      "condition_number": float, 单应性矩阵的条件数，指示数值稳定性
                          - 条件数越大，估计越不稳定
                          - 若 > 1e6，视为不稳定并降低置信度
      "determinant": float, 单应性矩阵行列式
                     - 接近 0 表示奇异或接近奇异
    }
    
        diagnostics: {
            "match_overlay_path": 对应点叠加图路径（green=inlier, red=outlier）
            "correspondences_csv_path": 对应点详细表路径
            "homography_csv_path": 单应性矩阵导出路径
            "match_result_json_path": 本 MatchResult 的 JSON 导出路径
            "failure_reason": 可选的失败原因短文本
        }
    """
    pair_id: str
    source_rgb: str
    source_thermal: str
    correspondences: List[Dict[str, float]]
    inlier_mask: List[bool]
    homography_matrix: List[List[float]]
    confidence: float
    match_quality: str
    runtime_ms: float
    algorithm_version: str
    outlier_ratio: float
    inlier_count: int
    outlier_count: int
    homography_stability: Dict[str, float]
    diagnostics: Dict[str, str]


class TwmmAdapter:
    """
    TWMM TAMM_clean 工作流的生产适配器。
    
    本类职责严格限制在 I/O 转换和流程编排：
    1. 延迟加载 TWMM-main/TAMM_clean 模块（避免强依赖）
    2. 执行去畸变双光谱图像对的匹配
    3. 将 TWMM 输出转换为 MatchResult 合约
    4. 生成诊断可视化和元数据
    
    核心流程（match_pair）：
    - ThermalVisble(crop_size, crop_offset, scale dict) 初始化
    - CFOG 特征提取（bin_size=9）
    - TAMM 多尺度模板匹配
    - filter_outliers 异常值剔除
    - findHomoraphy 单应性估计
    
    关键约束：
    - 所有参数（patch_size, search_radius, level_max, scale）必须来自 TwmmParams
    - TWMM 核心逻辑不作修改，只做适配
    - 失败时返回低置信结果，不伪造数据
    """

    def __init__(self, workspace_root: Path, twmm_root: Path) -> None:
        """
        初始化适配器。
        
        Args:
            workspace_root: 项目根路径，用于相对路径解析和诊断输出
            twmm_root: TWMM-main 根路径，应包含 TAMM_clean 子目录
        """
        self.workspace_root = workspace_root
        self.twmm_root = twmm_root
        # 延迟初始化 TWMM 模块，避免导入失败时强行失败
        self._thermal_visible_cls = None
        self._filter_outliers = None
        self._find_homography = None
        self._loaded = False

    def _load_twmm(self) -> None:
        """
        延迟加载 TWMM TAMM_clean 模块和关键函数。
        
        首次调用时通过动态 sys.path 注入和 importlib 导入 TAMM_clean 下的
        thermal_visible.py 和 image_process.py，缓存核心类和函数以提高性能。
        
        Raises:
            FileNotFoundError: 若 TAMM_clean 目录不存在
            ImportError: 若模块导入失败（应很少发生，因为代码已在 TAMM_clean 内）
        """
        if self._loaded:
            return

        tamm_clean_path = self.twmm_root / "TAMM_clean"
        if not tamm_clean_path.exists():
            raise FileNotFoundError(f"TAMM_clean not found: {tamm_clean_path}")

        # 注入 TAMM_clean 到 Python 路径，使其内部模块可被直接导入
        tamm_clean_str = str(tamm_clean_path)
        if tamm_clean_str not in sys.path:
            sys.path.insert(0, tamm_clean_str)

        # 导入核心模块：ThermalVisble 类（匹配流程主驱动）和辅助函数
        thermal_visible = importlib.import_module("thermal_visible")
        image_process = importlib.import_module("image_process")

        self._thermal_visible_cls = thermal_visible.ThermalVisble
        self._filter_outliers = image_process.filter_outliers
        self._find_homography = image_process.findHomoraphy
        self._loaded = True

    @staticmethod
    def _normalize_scores(scores: Sequence[float]) -> List[float]:
        """
        将匹配分数归一化到 [0, 1]。
        
        若所有分数相同（极差 < 1e-12），返回全 1.0 向量。
        否则使用 min-max 归一化：(s - s_min) / (s_max - s_min)。
        
        Args:
            scores: 原始分数序列
        
        Returns:
            归一化后的分数列表（float in [0, 1]）
        """
        if not scores:
            return []
        arr = np.array(scores, dtype=np.float64)
        s_min = float(arr.min())
        s_max = float(arr.max())
        # 若分数没有有效范围（所有值相同），返回中性值 1.0
        if abs(s_max - s_min) < 1e-12:
            return [1.0 for _ in scores]
        return [float((s - s_min) / (s_max - s_min)) for s in arr]

    @staticmethod
    def _homography_stability(h_mat: np.ndarray) -> Dict[str, float]:
        """
        评估单应性矩阵的数值稳定性。
        
        条件数 (condition number)：矩阵的最大奇异值与最小奇异值的比值。
        - 值越小，矩阵越良态（numerically stable）
        - 值 > 1e6 通常表示接近奇异且不稳定
        
        行列式 (determinant)：单应性的缩放因子。
        - 接近 0 表示近似奇异或退化
        - 用于快速判断是否需要重新估计
        
        Args:
            h_mat: 3x3 单应性矩阵
        
        Returns:
            {"condition_number": float, "determinant": float}
            若矩阵奇异，condition_number 返回 inf
        """
        try:
            cond = float(np.linalg.cond(h_mat))
        except np.linalg.LinAlgError:
            cond = math.inf
        det = float(np.linalg.det(h_mat))
        return {"condition_number": cond, "determinant": det}

    @staticmethod
    def _confidence(inlier_ratio: float, inlier_count: int, stable: bool) -> float:
        """
        融合多个质量指标计算置信度。
        
        置信度公式（权重和 = 1.0）：
        - 0.65 × 内点比例：衡量单应性的几何一致性
        - 0.25 × 点密度项：min(1, inlier_count / 80) 归一化点数
                         （80 为参考阈值，≥80 个内点视为最优）
        - 0.10 × 稳定性项：单应性矩阵的数值稳定性（stable 为 1 或 0.2）
        
        最终结果钳制到 [0, 1]。
        
        Args:
            inlier_ratio: float in [0, 1]，内点占候选点的比例
            inlier_count: int，内点绝对数量
            stable: bool，单应性矩阵是否稳定（条件数 < 1e6）
        
        Returns:
            float in [0, 1]，融合置信度
        """
        density_term = min(1.0, inlier_count / 80.0)
        stable_term = 1.0 if stable else 0.2
        return max(0.0, min(1.0, 0.65 * inlier_ratio + 0.25 * density_term + 0.10 * stable_term))

    @staticmethod
    def _quality_label(confidence: float, hard_fail: bool) -> str:
        """
        根据置信度和硬失败标记将匹配结果分类。
        
        分类规则（matching_algorithm.md § 5 约束）：
        - "low_confidence_failure": 硬失败（单应性求解失败、内点 < min_inliers、
                                  条件数 > homography_condition_max）
        - "high": confidence >= 0.8，高质量匹配
        - "medium": 0.6 <= confidence < 0.8，中等质量
        - "degraded": 0.4 <= confidence < 0.6，降级质量（可用但有风险）
        - "low": confidence < 0.4，低质量（不建议用于后续处理）
        
        Args:
            confidence: float in [0, 1]
            hard_fail: bool，是否存在硬失败条件
        
        Returns:
            str，质量标签
        """
        if hard_fail:
            return "low_confidence_failure"
        if confidence >= 0.8:
            return "high"
        if confidence >= 0.6:
            return "medium"
        if confidence >= 0.4:
            return "degraded"
        return "low"

    @staticmethod
    def _build_overlay(
        rgb_img: np.ndarray,
        thermal_img: np.ndarray,
        correspondences: Iterable[Dict[str, float]],
        out_path: Path,
    ) -> None:
        def _to_bgr(display_img: np.ndarray) -> np.ndarray:
            normalized = display_img.copy()
            if normalized.dtype != np.uint8:
                normalized = cv2.normalize(normalized, None, 0, 255, cv2.NORM_MINMAX)
                normalized = normalized.astype(np.uint8)
            if normalized.ndim == 2:
                normalized = cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR)
            elif normalized.ndim == 3 and normalized.shape[2] == 1:
                normalized = cv2.cvtColor(normalized[:, :, 0], cv2.COLOR_GRAY2BGR)
            return normalized

        rgb = _to_bgr(rgb_img)
        thermal = _to_bgr(thermal_img)
        for row in correspondences:
            rx = int(round(row["rgb_x"]))
            ry = int(round(row["rgb_y"]))
            tx = int(round(row["thermal_x"]))
            ty = int(round(row["thermal_y"]))
            color = (0, 255, 0) if row["is_inlier"] else (0, 0, 255)
            cv2.circle(rgb, (rx, ry), 2, color, -1)
            cv2.circle(thermal, (tx, ty), 2, color, -1)

        h = max(rgb.shape[0], thermal.shape[0])
        w = rgb.shape[1] + thermal.shape[1]
        canvas = np.zeros((h, w, 3), dtype=np.uint8)
        canvas[: rgb.shape[0], : rgb.shape[1]] = rgb
        canvas[: thermal.shape[0], rgb.shape[1] : rgb.shape[1] + thermal.shape[1]] = thermal
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), canvas)

    def match_pair(
        self,
        rgb_path: Path,
        thermal_tiff_path: Path,
        thermal_preview_path: Optional[Path],
        params: TwmmParams,
        output_dir: Path,
        pair_id: Optional[str] = None,
    ) -> MatchResult:
        self._load_twmm()
        params.validate()

        if not rgb_path.exists():
            raise FileNotFoundError(f"RGB image not found: {rgb_path}")
        if not thermal_tiff_path.exists():
            raise FileNotFoundError(f"Thermal TIFF image not found: {thermal_tiff_path}")

        preview_path = thermal_preview_path if thermal_preview_path and thermal_preview_path.exists() else None
        pair_name = pair_id or thermal_tiff_path.stem
        pair_out = output_dir / pair_name
        ensure_dir(pair_out)

        start = time.perf_counter()

        thermal_visible = self._thermal_visible_cls(
            thermal_tiff_path=str(thermal_tiff_path),
            thermal_rgb_path=str(thermal_tiff_path),
            visible_rgb_path=str(rgb_path),
            crop_size=params.crop_size,
            scale=params.scale,
            attention_flag=False,
            crop_offset=params.crop_offset,
        )

        thermal_visible.get_img_features(method="CFOG", bin_size=9)
        raw_corres = thermal_visible.get_correspoints(
            method="TAMM",
            patch_size=params.patch_size,
            search_radius=params.search_radius,
            level_max=params.level_max,
        )

        thermal_points: List[Tuple[float, float]] = []
        visible_points: List[Tuple[float, float]] = []
        scores: List[float] = []

        for thermal_rc, data in raw_corres.items():
            visible_rc, score = data
            thermal_points.append((float(thermal_rc[0]), float(thermal_rc[1])))
            visible_points.append((float(visible_rc[0]), float(visible_rc[1])))
            scores.append(float(score))

        local_conf = self._normalize_scores(scores)

        thermal_for_filter = thermal_points.copy()
        visible_for_filter = visible_points.copy()
        inlier_t, inlier_v, outlier_t, outlier_v = self._filter_outliers(
            thermal_for_filter,
            visible_for_filter,
            thresh=params.outlier_threshold_px,
            method="NBCS",
        )

        inlier_pairs = set(zip(inlier_t, inlier_v))
        correspondences: List[Dict[str, float]] = []
        inlier_mask: List[bool] = []

        for idx, (trc, vrc) in enumerate(zip(thermal_points, visible_points)):
            is_inlier = (trc, vrc) in inlier_pairs
            inlier_mask.append(is_inlier)
            correspondences.append(
                {
                    "rgb_x": float(vrc[1]),
                    "rgb_y": float(vrc[0]),
                    "thermal_x": float(trc[1]),
                    "thermal_y": float(trc[0]),
                    "is_inlier": bool(is_inlier),
                    "pyramid_level": float(params.level_max),
                    "patch_size": float(params.patch_size),
                    "score": float(scores[idx]),
                    "local_confidence": float(local_conf[idx]),
                }
            )

        good = [[v[1], v[0], t[1], t[0]] for t, v in zip(inlier_t, inlier_v)]
        h_mat, homo_ok = self._find_homography(good)
        h_mat = np.array(h_mat, dtype=np.float64)

        inlier_count = len(inlier_t)
        total_count = max(1, len(thermal_points))
        outlier_count = max(0, len(thermal_points) - inlier_count)
        outlier_ratio = float(outlier_count / total_count)
        stability = self._homography_stability(h_mat)
        stable = math.isfinite(stability["condition_number"]) and stability["condition_number"] <= params.homography_condition_max
        hard_fail = (not bool(homo_ok)) or (inlier_count < params.min_inliers) or (not stable)

        confidence = self._confidence(1.0 - outlier_ratio, inlier_count, stable)
        quality = self._quality_label(confidence, hard_fail)

        runtime_ms = (time.perf_counter() - start) * 1000.0

        overlay_path = pair_out / "match_overlay.png"
        self._build_overlay(thermal_visible.visible_rgb, thermal_visible.thermal_rgb, correspondences, overlay_path)

        homography_path = pair_out / "homography_matrix.csv"
        write_csv_rows(
            homography_path,
            ["h11", "h12", "h13"],
            [[float(x) for x in h_mat[0]], [float(x) for x in h_mat[1]], [float(x) for x in h_mat[2]]],
        )

        corr_path = pair_out / "correspondences.csv"
        write_csv_rows(
            corr_path,
            [
                "rgb_x",
                "rgb_y",
                "thermal_x",
                "thermal_y",
                "is_inlier",
                "pyramid_level",
                "patch_size",
                "score",
                "local_confidence",
            ],
            [
                [
                    c["rgb_x"],
                    c["rgb_y"],
                    c["thermal_x"],
                    c["thermal_y"],
                    c["is_inlier"],
                    c["pyramid_level"],
                    c["patch_size"],
                    c["score"],
                    c["local_confidence"],
                ]
                for c in correspondences
            ],
        )

        result_json_path = pair_out / "match_result.json"

        result = MatchResult(
            pair_id=pair_name,
            source_rgb=str(rgb_path),
            source_thermal=str(thermal_tiff_path),
            correspondences=correspondences,
            inlier_mask=inlier_mask,
            homography_matrix=h_mat.tolist(),
            confidence=float(confidence),
            match_quality=quality,
            runtime_ms=float(runtime_ms),
            algorithm_version="TWMM.TAMM_clean.CFOG+TAMM.v1",
            outlier_ratio=float(outlier_ratio),
            inlier_count=int(inlier_count),
            outlier_count=int(outlier_count),
            homography_stability=stability,
            diagnostics={
                "match_overlay_path": str(overlay_path),
                "correspondences_csv_path": str(corr_path),
                "homography_csv_path": str(homography_path),
                "match_result_json_path": str(result_json_path),
                "thermal_preview_path": str(preview_path) if preview_path is not None else "",
                "failure_reason": "" if not hard_fail else "homography_failed_or_unstable_or_low_inliers",
            },
        )
        write_json(result_json_path, asdict(result))
        return result

    @staticmethod
    def _find_existing_rgb_path(rgb_dir: Path, visible_name: str) -> Optional[Path]:
        stem = Path(visible_name).stem
        suffixes = [".png", ".jpg", ".jpeg", ".tif", ".tiff"]
        for suffix in suffixes:
            candidate = rgb_dir / f"{stem}{suffix}"
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _pair_id_from_thermal(thermal_tiff_path: Path) -> str:
        return thermal_tiff_path.stem

    @staticmethod
    def _map_visible_filename_from_thermal(thermal_tiff_name: str) -> str:
        # DJI_..._0001_T.tif -> DJI_..._0001_W.JPG
        stem = Path(thermal_tiff_name).stem
        if stem.endswith("_T"):
            return stem[:-2] + "_W.JPG"
        return stem + "_W.JPG"

    @staticmethod
    def _map_preview_filename_from_thermal(thermal_tiff_name: str) -> str:
        # DJI_..._0001_T.tif -> DJI_..._0001_T.JPG
        stem = Path(thermal_tiff_name).stem
        return stem + ".JPG"

    def match_batch_from_dirs(
        self,
        rgb_dir: Path,
        thermal_tiff_dir: Path,
        thermal_preview_dir: Path,
        params: TwmmParams,
        output_dir: Path,
        max_pairs: Optional[int] = None,
    ) -> List[MatchResult]:
        ensure_dir(output_dir)
        thermal_files = sorted(p for p in thermal_tiff_dir.glob("*.tif")) + sorted(p for p in thermal_tiff_dir.glob("*.tiff"))

        results: List[MatchResult] = []
        for idx, thermal_path in enumerate(thermal_files):
            if max_pairs is not None and idx >= max_pairs:
                break

            rgb_name = self._map_visible_filename_from_thermal(thermal_path.name)
            preview_name = self._map_preview_filename_from_thermal(thermal_path.name)
            rgb_path = self._find_existing_rgb_path(rgb_dir, rgb_name)
            preview_path = thermal_preview_dir / preview_name

            if rgb_path is None:
                continue

            pair_result = self.match_pair(
                rgb_path=rgb_path,
                thermal_tiff_path=thermal_path,
                thermal_preview_path=preview_path,
                params=params,
                output_dir=output_dir,
                pair_id=self._pair_id_from_thermal(thermal_path),
            )
            results.append(pair_result)

        summary_path = output_dir / "matching_summary.json"
        write_json(summary_path, {
            "total_pairs": len(results),
            "results": [asdict(r) for r in results],
        })
        return results

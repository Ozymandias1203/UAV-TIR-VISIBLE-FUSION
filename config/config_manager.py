import os
import json
import hashlib
import sys
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Sequence
import logging

logger = logging.getLogger(__name__)

@dataclass
class DatasetProfile:
    """定义 dataset_profile 的默认物理事实与别名，提供向后兼容字段。"""
    dataset_id: str = "test_Arctic"
    rgb_resolution: tuple = (4032, 3024)
    thermal_resolution: tuple = (1280, 1024)
    # 物理格数（格子数量，非内角点），以及内角点数（OpenCV 需要的 pattern_size）
    chessboard_physical: tuple = (12, 9)
    chessboard_size: tuple = (11, 8)
    chessboard_corners: tuple = (12, 9)
    # 可选的先验相机矩阵（若提供则可作为临时默认）
    rgb_camera_matrix: Optional[List[List[float]]] = None
    thermal_camera_matrix: Optional[List[List[float]]] = None
    square_size_mm: Optional[float] = 40.0


@dataclass
class RuntimeConfig:
    """简化的运行时配置结构，包含常用路径与参数。

    注意：字段名称尽量保持扁平并与流水线调用处匹配（例如 data_dir_rgb）。
    """
    workspace_root: str
    dataset_id: str
    input_dataset_root: str
    calibration_dataset_root: str
    output_root: str
    run_id: str
    pipeline_version: str
    stage_selection: List[str]
    quality_thresholds: Dict[str, float]
    environment_parameters: Dict[str, float]
    toolchain_versions: Dict[str, str]
    data_dir_rgb: str
    data_dir_thermal_tiff: str


class ConfigManager:
    """配置与数据入口层，兼容脚本与各阶段调用。

    特性：
    - 接受 dict 或字符串参数（字符串可为工作目录或 JSON 配置文件路径）；
    - 提供 `dataset_profile`、`runtime_config`、`get_calibration_output_path()`、`_resolve_path()` 等接口；
    - 自动创建运行目录与常用子目录并导出运行清单。
    """

    def __init__(self, user_config: Any = None, **kwargs):
        # 允许传入字符串（工作目录或 JSON 配置文件路径）、dict，或以关键字参数形式传入
        cfg: Dict[str, Any] = {}

        # 先合并关键字参数（如 workspace_root='.'）
        if kwargs:
            cfg.update(kwargs)

        # 再处理显式传入的 user_config 优先级更高
        if user_config is None:
            pass
        elif isinstance(user_config, str):
            # 如果是文件路径，尝试解析 JSON 配置
            if os.path.isfile(user_config):
                try:
                    with open(user_config, 'r', encoding='utf-8') as f:
                        file_cfg = json.load(f)
                        cfg.update(file_cfg)
                except Exception:
                    cfg.setdefault('workspace_root', os.path.abspath(os.path.dirname(user_config)))
            else:
                cfg.setdefault('workspace_root', user_config)
        elif isinstance(user_config, dict):
            cfg.update(user_config)
        else:
            raise ValueError('user_config must be a dict, a path string, or omitted')

        self.user_config = dict(cfg)

        # dataset_profile（不可变事实）
        self.dataset_profile = DatasetProfile()

        # 构建 runtime_config 并建立工作目录
        self.runtime_config = self._build_runtime_config(cfg)
        # 兼容旧代码：self.config
        self.config = self.runtime_config
        self.workspace_root = self.runtime_config.workspace_root
        self.run_id = self.runtime_config.run_id
        self.output_root = self.runtime_config.output_root
        self.run_dir = os.path.join(self.runtime_config.output_root, self.runtime_config.run_id)

        # 常用目录句柄
        self.dirs: Dict[str, str] = {}
        self.setup_workspace()

    @staticmethod
    def _normalize_stage_name(stage_name: str) -> str:
        normalized = str(stage_name).strip().lower()
        if normalized == 'reconstruction':
            return 'metashape'
        return normalized

    @classmethod
    def _normalize_stage_selection(cls, stage_selection: Optional[Sequence[str]]) -> List[str]:
        allowed = {
            'config',
            'calibration',
            'preprocess',
            'matching',
            'radiometry',
            'metashape',
            'geometry',
            'enrichment',
            'validation',
            'io',
        }

        if isinstance(stage_selection, str):
            stage_selection = [stage_selection]

        normalized: List[str] = []
        seen = set()
        for stage_name in stage_selection or []:
            canonical_name = cls._normalize_stage_name(stage_name)
            if canonical_name not in allowed:
                raise ValueError(f'Unknown stage name in stage_selection: {stage_name}')
            if canonical_name in seen:
                continue
            normalized.append(canonical_name)
            seen.add(canonical_name)
        return normalized

    def _build_runtime_config(self, user_cfg: Dict[str, Any]) -> RuntimeConfig:
        timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        canonical_cfg = dict(user_cfg)
        if 'stage_selection' in canonical_cfg:
            canonical_cfg['stage_selection'] = self._normalize_stage_selection(canonical_cfg.get('stage_selection'))
        config_str = json.dumps(canonical_cfg, sort_keys=True, default=str)
        config_sha8 = hashlib.sha256(config_str.encode('utf-8')).hexdigest()[:8]

        dataset_id = user_cfg.get('dataset_id', self.dataset_profile.dataset_id)
        run_id = user_cfg.get('run_id', f"{dataset_id}__{timestamp}__{config_sha8}")

        workspace_root = user_cfg.get('workspace_root', os.getcwd())
        output_root = user_cfg.get('output_root', os.path.join(workspace_root, 'runs'))

        input_dataset_root = user_cfg.get('input_dataset_root', os.path.join(workspace_root, 'test_Arctic'))
        calibration_dataset_root = user_cfg.get('calibration_dataset_root', os.path.join(workspace_root, 'M400-H30T-CALIB-CHESSBOARD'))

        data_dir_rgb = user_cfg.get('data_dir_rgb', os.path.join(input_dataset_root, 'rgb_dir'))
        # 默认 thermal tif 目录使用 tiff_dir，thermal_dir 仅作为预览目录。
        data_dir_thermal_tiff = user_cfg.get('data_dir_thermal_tiff', os.path.join(input_dataset_root, 'tiff_dir'))

        default_toolchain_versions = {
            'python_version': f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',
            'metashape_version': '2.2.1',
            'twmm_source_ref': 'TWMM-main/TAMM_clean',
            'exiftool_version': '13.48',
            'pipeline_schema_version': '1.0.0',
        }
        user_toolchain_versions = dict(user_cfg.get('toolchain_versions') or {})
        default_toolchain_versions.update(user_toolchain_versions)

        return RuntimeConfig(
            workspace_root=workspace_root,
            dataset_id=dataset_id,
            input_dataset_root=input_dataset_root,
            calibration_dataset_root=calibration_dataset_root,
            output_root=output_root,
            run_id=run_id,
            pipeline_version=user_cfg.get('pipeline_version', '1.0.0'),
            stage_selection=self._normalize_stage_selection(user_cfg.get('stage_selection', [
                'config', 'calibration', 'preprocess', 'matching', 'radiometry', 'metashape', 'geometry', 'enrichment', 'validation'
            ])),
            quality_thresholds=user_cfg.get('quality_thresholds', {}),
            environment_parameters=user_cfg.get('environment_parameters', {}),
            toolchain_versions=default_toolchain_versions,
            data_dir_rgb=data_dir_rgb,
            data_dir_thermal_tiff=data_dir_thermal_tiff
        )

    def setup_workspace(self):
        os.makedirs(self.run_dir, exist_ok=True)
        # 常用子目录
        self.dirs = {
            'manifest': os.path.join(self.run_dir, 'manifest'),
            'calibration': os.path.join(self.run_dir, 'calibration'),
            'undistorted': os.path.join(self.run_dir, 'undistorted'),
            'preprocess': os.path.join(self.run_dir, 'preprocess'),
            'matching': os.path.join(self.run_dir, 'matching'),
            'radiometry': os.path.join(self.run_dir, 'radiometry'),
            'metashape': os.path.join(self.run_dir, 'metashape'),
            'reconstruction': os.path.join(self.run_dir, 'metashape'),
            'geometry': os.path.join(self.run_dir, 'geometry'),
            'enrichment': os.path.join(self.run_dir, 'enrichment'),
            'validation': os.path.join(self.run_dir, 'validation'),
            'reports': os.path.join(self.run_dir, 'reports')
        }

        for p in self.dirs.values():
            os.makedirs(p, exist_ok=True)

        # 生成运行清单（轻量版）
        self._generate_run_manifest()

    def _generate_run_manifest(self):
        manifest_path = os.path.join(self.dirs['manifest'], 'run_manifest.json')
        manifest = {
            'run_id': self.runtime_config.run_id,
            'dataset_id': self.runtime_config.dataset_id,
            'created_at': datetime.utcnow().isoformat() + 'Z',
            'pipeline_version': self.runtime_config.pipeline_version,
            'input_paths': {
                'business_data': self.runtime_config.input_dataset_root,
                'calibration_data': self.runtime_config.calibration_dataset_root
            },
            'output_paths': {
                'run_root': self.run_dir
            },
            'stage_status': {s: 'pending' for s in self.runtime_config.stage_selection},
            'runtime_config_snapshot': asdict(self.runtime_config)
        }

        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to generate run manifest at {manifest_path}: {e}")

    def _resolve_path(self, path_like: str) -> str:
        if not path_like:
            return self.workspace_root
        if os.path.isabs(path_like):
            return path_like
        return os.path.abspath(os.path.join(self.workspace_root, path_like))

    def get_stage_dir(self, stage_name: str) -> str:
        return self.dirs.get(stage_name, os.path.join(self.run_dir, stage_name))

    def get_calibration_output_path(self) -> str:
        return os.path.join(self.dirs['calibration'], f"sensors_calibration_{self.runtime_config.run_id}.json")

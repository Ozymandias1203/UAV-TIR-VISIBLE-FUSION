"""
运行时配置数据模型与契约。

本模块定义了两个跨光谱匹配与热辐射处理层的通用配置类，以及整体项目运行时配置。
所有配置参数都通过数据类静态检验，确保下游计算接收有效输入。

配置验证原则：
- TwmmParams.validate(): 检查 TWMM 参数的合理性（patch_size > 0, scale dict 含必要键）
- EnvironmentParameters.missing_required(): 标记缺失的必需环保参数
- RuntimeConfig.validate_stage_selection(): 检查阶段名称合法性

设计文档参考：
- AGENTS.md § 4-5: 配置与数据入口层职责、两类数据区分
- runtime_config.md: 运行配置与参数校验的单一来源
- matching_algorithm.md § 3: TwmmParams 输入契约
- radiometry_model.md § 3: EnvironmentParameters 来源与优先级
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


class ConfigError(ValueError):
    """Raised when runtime config does not satisfy project contract."""


@dataclass
class TwmmParams:
    """
    跨光谱模板匹配参数（matching_algorithm.md § 3、AGENTS.md § 4）。
    
    所有参数必须显式来自运行配置，不能靠代码内硬编码。scale dict 必须包含
    'thermal' 和 'visible' 两个键，以支持双光谱尺度不匹配的适配。
    
    参数语义：
    
    patch_size: int > 0
      原子块边长（像素），用于 TWMM 第一步的相似度图构建。
      典型值：40（与标准 TWMM 论文参数对标）
    
    search_radius: int > 0
      搜索半径（像素），限制候选对应点的搜索范围。
      典型值：60
    
    level_max: int > 0
      金字塔最大层数，控制多尺度相似度图的深度。
      典型值：4（从细到粗逐层池化）
    
    crop_size: Tuple[int, int]
      感兴趣区域（ROI）裁剪尺寸。(H, W) 格式，单位：像素。
      若不需要裁剪，可设置为原图尺寸。
    
    crop_offset: Tuple[int, int] = (0, 0)
      ROI 裁剪相对于原图左上角的偏移量。(dy, dx) 格式。默认无偏移。
    
    scale: Dict[str, float] = {'thermal': 1.0, 'visible': 1.0}
      双光谱尺度适配系数。'thermal' 和 'visible' 键必须同时存在。
      值表示该通道的上采样/下采样倍数。1.0 表示无缩放。
    
    outlier_threshold_px: float = 2.0
      外点剔除阈值（像素）。单应性变换后的残差超过此值被标记为外点。
    
    min_inliers: int = 8
      最少内点数。若内点数 < 此值，match_pair 将返回 low_confidence_failure。
    
    homography_condition_max: float = 1e6
      单应性矩阵条件数上界。若条件数 > 此值，矩阵判定为数值不稳定。
    """
    patch_size: int
    search_radius: int
    level_max: int
    crop_size: Tuple[int, int]
    crop_offset: Tuple[int, int] = (0, 0)
    scale: Dict[str, float] = field(default_factory=lambda: {"thermal": 1.0, "visible": 1.0})
    outlier_threshold_px: float = 2.0
    min_inliers: int = 8
    homography_condition_max: float = 1e6

    def validate(self) -> None:
        """
        校验参数的合理性。
        
        Raises:
            ConfigError: 若参数不符合下列条件
              - patch_size > 0
              - search_radius > 0
              - level_max > 0
              - crop_size 为 (H, W) 且 H > 0, W > 0
              - scale 是字典且包含 'thermal' 和 'visible' 键
        """
        if self.patch_size <= 0:
            raise ConfigError("patch_size must be > 0")
        if self.search_radius <= 0:
            raise ConfigError("search_radius must be > 0")
        if self.level_max <= 0:
            raise ConfigError("level_max must be > 0")
        if len(self.crop_size) != 2 or min(self.crop_size) <= 0:
            raise ConfigError("crop_size must be a tuple of two positive integers")
        if not isinstance(self.scale, dict):
            raise ConfigError("scale must be a dict with keys: thermal and visible")
        if "thermal" not in self.scale or "visible" not in self.scale:
            raise ConfigError("scale must contain thermal and visible")


@dataclass
class EnvironmentParameters:
    """
    辐射校正所需的环境参数（radiometry_model.md § 3）。
    
    这些参数用于从热红外 TIFF 原始值计算最终温度值。它们必须由外部输入提供
    （如现场测量或配置文件），而不能从设备元数据或图像推导。
    
    缺失的必需参数会导致 quality_flag = "degraded" 或 "invalid"，但不会伪造数据。
    
        必需参数（missing_required() 检查）：

        ambient_temperature_celsius: 环境/背景温度（摄氏度）。用于计算大气路径辐射。
        relative_humidity_percent: 相对湿度（0-100%）。用于计算大气透射率衰减系数。
        emissivity_ratio: 目标物体发射率（0-1）。典型值 0.95（黑体近似）。
        distance_to_target_m: 传感器到目标距离（米）。影响大气透射率衰减。

        项目默认值：为便于快速试验，本项目对上述环境参数设定了默认值（仅作为默认试验参数，真实运行应显式提供）：
            - ambient_temperature_celsius = -1.0  # 摄氏度（示例：海冰场景）
            - relative_humidity_percent = 70.0    # %%
            - emissivity_ratio = 0.97             # 海冰典型发射率
            - distance_to_target_m = 100.0        # 典型航高/目标距离（米）
        请在正式运行或精确测量场景中用外部测量值覆盖这些默认值；缺省使用可能导致精度下降。
    
    可选参数（用于改进精度但非必需）：
    
    reflected_temperature_celsius: 反射温度。若无提供，使用 ambient_temperature_celsius。
    atmospheric_pressure_hpa: 大气压。若无提供，使用标准海平面气压 1013.25 hPa。
    source: 参数来源标记，如 "manual" / "config_file"（用于审计）。
    source_ref: 参数来源引用（文件路径或时间戳），便于追踪。
    """
    ambient_temperature_celsius: Optional[float] = -1.0
    relative_humidity_percent: Optional[float] = 70.0
    emissivity_ratio: Optional[float] = 0.97
    distance_to_target_m: Optional[float] = 100.0
    reflected_temperature_celsius: Optional[float] = None
    atmospheric_pressure_hpa: Optional[float] = None
    source: str = "manual"
    source_ref: Optional[str] = None

    def missing_required(self) -> List[str]:
        """
        标记缺失的必需环保参数。
        
        Returns:
            缺失参数名列表（可能为空）。若列表非空，调用方应降级处理或标记质量。
        """
        missing = []
        if self.ambient_temperature_celsius is None:
            missing.append("ambient_temperature_celsius")
        if self.relative_humidity_percent is None:
            missing.append("relative_humidity_percent")
        if self.emissivity_ratio is None:
            missing.append("emissivity_ratio")
        if self.distance_to_target_m is None:
            missing.append("distance_to_target_m")
        return missing


@dataclass
class RadiometryParams:
    metadata_json_path: Path
    processing_version: str = "radiometry.v1"
    temperature_unit: str = "celsius"
    input_is_temperature: bool = False
    raw_to_temperature_scale: float = 0.04
    raw_to_temperature_offset: float = -273.15
    valid_range_celsius: Tuple[float, float] = (-80.0, 300.0)


@dataclass
class RuntimeConfig:
    workspace_root: Path
    output_root: Path
    dataset_id: str
    run_id: str
    pipeline_version: str
    stage_selection: List[str]
    environment_parameters: EnvironmentParameters
    twmm_params: Optional[TwmmParams] = None
    radiometry_params: Optional[RadiometryParams] = None
    quality_thresholds: Dict[str, Any] = field(default_factory=dict)
    toolchain_versions: Dict[str, Any] = field(default_factory=dict)

    def validate_stage_selection(self) -> None:
        valid_stages = {
            "config",
            "calibration",
            "preprocess",
            "matching",
            "radiometry",
            "metashape",
            "reconstruction",
            "geometry",
            "enrichment",
            "io",
            "validation",
        }
        unknown = [s for s in self.stage_selection if s not in valid_stages]
        if unknown:
            raise ConfigError(f"Unknown stage names in stage_selection: {unknown}")


def require_existing_paths(paths: Iterable[Path], label: str) -> None:
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise ConfigError(f"{label} missing paths: {missing}")


def parse_environment_parameters(raw: Mapping[str, Any]) -> EnvironmentParameters:
    # 使用项目默认值作为回退，当 raw 中未提供某些字段时使用这些默认值
    return EnvironmentParameters(
        ambient_temperature_celsius=raw.get("ambient_temperature_celsius", -1.0),
        relative_humidity_percent=raw.get("relative_humidity_percent", 70.0),
        emissivity_ratio=raw.get("emissivity_ratio", 0.97),
        distance_to_target_m=raw.get("distance_to_target_m", 100.0),
        reflected_temperature_celsius=raw.get("reflected_temperature_celsius"),
        atmospheric_pressure_hpa=raw.get("atmospheric_pressure_hpa"),
        source=raw.get("source", "manual"),
        source_ref=raw.get("source_ref"),
    )

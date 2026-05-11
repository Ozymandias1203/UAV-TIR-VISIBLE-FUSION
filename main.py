"""
# 双光谱热红外与可见光三维点云生成程序入口 (Main Orchestrator)
# ==============================================================
# 此文件作为本项目的全局核心启动器，等效于 C 语言工程中的 main() 函数文件。
# 它负责串联整个端到端的处理流水线（Pipeline），从原始的无人机影像文件到最终带有温度信息的三维点云。
# 
# 按照在 AGENTS.md 和 docs/architecture.md 中定义的设计，全链路包含如下阶段：
# 0. main.py / Configuration (全局参数、环境设置)
# 1. Calibration (双光谱系统标定)
# 2. Preprocess (影像去畸变与预处理)
# 3. Matching (基于 TWMM 的跨光谱匹配与单应性估计)
# 4. Radiometry (热红外辐射校正与大面积温度提取)
# 5. Metashape (基于 Metashape 的多视角三维可见光网格重建)
# 6. Reprojection & Visibility (可见光重投影与遮挡判断)
# 7. Enrichment (借由匹配对应的热红外信息完成点云热富集赋值)
# 8. Export & Quality Check (结果输出与质检导出)
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from calibration.calibrator import DualSpectralCalibrator
from config.runtime_models import EnvironmentParameters, RadiometryParams, TwmmParams
from config.config_manager import ConfigManager
from enrichment.thermal_enrichment import ThermalEnricher
from geometry.reprojection_export import ReprojectionExporter
from matching import TwmmAdapter
from pipeline_io.json_io import read_json, write_json
from preprocess.undistort import ImageUndistorter
from radiometry import RadiometryPipeline
from validation.evaluation import export_thermal_point_cloud_ply


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def _ensure_path(path_value: Any) -> Path:
    return path_value if isinstance(path_value, Path) else Path(str(path_value))


def _collect_images(directory: Path, suffixes: Optional[Sequence[str]] = None) -> List[Path]:
    suffix_set = {suffix.lower() for suffix in (suffixes or IMAGE_SUFFIXES)}
    return sorted(
        path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in suffix_set
    )


def _load_stage_manifest(manager: ConfigManager) -> Dict[str, Any]:
    manifest_path = Path(manager.dirs["manifest"]) / "run_manifest.json"
    if manifest_path.exists():
        return read_json(manifest_path)
    return {}


def _update_stage_manifest(manager: ConfigManager, stage_name: str, status: str, **details: Any) -> None:
    manifest_path = Path(manager.dirs["manifest"]) / "run_manifest.json"
    manifest = _load_stage_manifest(manager)
    manifest.setdefault("stage_status", {})[stage_name] = status
    manifest.setdefault("stage_details", {})[stage_name] = details
    manifest["last_updated_at"] = ""
    write_json(manifest_path, manifest)


def _find_sensor_images(directory: Path, sensor_name: str) -> List[Path]:
    images = _collect_images(directory)
    sensor_name_upper = sensor_name.upper()
    filtered = [path for path in images if sensor_name_upper in path.stem.upper()]
    return filtered if filtered else images


def _load_calibration_bundle(calibration_output_path: Path) -> Dict[str, Any]:
    if not calibration_output_path.exists():
        raise FileNotFoundError(f"Calibration bundle not found: {calibration_output_path}")
    return read_json(calibration_output_path)


def _load_initial_calibration_xml(xml_path: Path, sensor_name: str) -> Dict[str, Any]:
    if not xml_path.exists():
        raise FileNotFoundError(f"Initial calibration XML not found: {xml_path}")

    root = ET.parse(xml_path).getroot()

    def _text(tag_name: str, default: Optional[str] = None) -> Optional[str]:
        element = root.find(tag_name)
        if element is None or element.text is None:
            return default
        return element.text.strip()

    width = int(float(_text("width", "0") or 0))
    height = int(float(_text("height", "0") or 0))
    focal_length = float(_text("f", "0") or 0)
    center_x = float(_text("cx", "0") or 0)
    center_y = float(_text("cy", "0") or 0)
    skew = float(_text("b1", "0") or 0)
    distortion_coefficients = [
        float(_text("k1", "0") or 0),
        float(_text("k2", "0") or 0),
        0.0,
        0.0,
        float(_text("k3", "0") or 0),
    ]

    principal_point_x = width / 2.0 + center_x if width else center_x
    principal_point_y = height / 2.0 + center_y if height else center_y

    return {
        "schema_version": "calibration_v1",
        "source_version": "H30T_XML",
        "created_at": _text("date") or "",
        "sensor_name": sensor_name,
        "image_width_px": width or None,
        "image_height_px": height or None,
        "focal_length_px": [focal_length, focal_length] if focal_length else [],
        "principal_point_x_px": principal_point_x,
        "principal_point_y_px": principal_point_y,
        "affinity_or_skew": skew,
        "distortion_coefficients": distortion_coefficients,
        "reprojection_rms_px": None,
        "chessboard_detection_rate": 0.0,
        "quality_flag": "FALLBACK_XML",
        "units": {
            "focal_length_px": "pixels",
            "principal_point_x_px": "pixels",
            "principal_point_y_px": "pixels",
            "reprojection_rms_px": "pixels",
        },
        "source_images": [],
        "calibration_source": str(xml_path),
    }


def _prepare_twmm_params(manager: ConfigManager) -> TwmmParams:
    quality_thresholds = manager.runtime_config.quality_thresholds or {}
    twmm_cfg = manager.user_config.get("twmm", {}) if hasattr(manager, "user_config") else {}
    return TwmmParams(
        patch_size=int(twmm_cfg.get("patch_size", 40)),
        search_radius=int(twmm_cfg.get("search_radius", 60)),
        level_max=int(twmm_cfg.get("level_max", 4)),
        crop_size=tuple(twmm_cfg.get("crop_size", (1024, 1024))),
        crop_offset=tuple(twmm_cfg.get("crop_offset", (0, 0))),
        scale={
            "thermal": float(twmm_cfg.get("thermal_scale", 1.0)),
            "visible": float(twmm_cfg.get("visible_scale", 1.0)),
        },
        outlier_threshold_px=float(quality_thresholds.get("matching_outlier_threshold_px", twmm_cfg.get("outlier_threshold_px", 2.0))),
        min_inliers=int(quality_thresholds.get("matching_min_inliers", twmm_cfg.get("min_inliers", 8))),
        homography_condition_max=float(quality_thresholds.get("homography_condition_max", twmm_cfg.get("homography_condition_max", 1e6))),
    )


def _prepare_environment(manager: ConfigManager) -> EnvironmentParameters:
    env_cfg = manager.user_config.get("environment_parameters", {}) if hasattr(manager, "user_config") else {}
    if not isinstance(env_cfg, dict):
        env_cfg = {}
    return EnvironmentParameters(
        ambient_temperature_celsius=env_cfg.get("ambient_temperature_celsius"),
        relative_humidity_percent=env_cfg.get("relative_humidity_percent"),
        emissivity_ratio=env_cfg.get("emissivity_ratio"),
        distance_to_target_m=env_cfg.get("distance_to_target_m"),
        reflected_temperature_celsius=env_cfg.get("reflected_temperature_celsius"),
        atmospheric_pressure_hpa=env_cfg.get("atmospheric_pressure_hpa"),
        source=str(env_cfg.get("source", manager.user_config.get("environment_source", "manual"))),
        source_ref=env_cfg.get("source_ref"),
    )


def _prepare_radiometry_params(manager: ConfigManager) -> RadiometryParams:
    rad_cfg = manager.user_config.get("radiometry", {}) if hasattr(manager, "user_config") else {}
    metadata_json = manager.user_config.get("metadata_json", str(Path(manager.workspace_root) / "metadata_all.json"))
    return RadiometryParams(
        metadata_json_path=_ensure_path(metadata_json),
        processing_version=str(rad_cfg.get("processing_version", "radiometry.v1")),
        temperature_unit=str(rad_cfg.get("temperature_unit", "celsius")),
        input_is_temperature=bool(rad_cfg.get("input_is_temperature", False)),
        raw_to_temperature_scale=float(rad_cfg.get("raw_to_temperature_scale", 0.04)),
        raw_to_temperature_offset=float(rad_cfg.get("raw_to_temperature_offset", -273.15)),
        valid_range_celsius=tuple(rad_cfg.get("valid_range_celsius", (-80.0, 300.0))),
    )


def _build_undistorted_path(base_dir: Path, source_path: Path, is_tir: bool) -> Path:
    output_name = source_path.stem + (".tiff" if is_tir else ".png")
    return base_dir / output_name


def _load_radiometry_temperature_maps(results: Iterable[Any]) -> Dict[str, np.ndarray]:
    thermal_maps: Dict[str, np.ndarray] = {}
    for result in results:
        matrix_path = Path(result.temperature_matrix)
        if matrix_path.exists():
            thermal_maps[result.frame_id] = np.load(matrix_path)
    return thermal_maps


def _load_matching_homographies(results: Iterable[Any]) -> Dict[str, np.ndarray]:
    homographies: Dict[str, np.ndarray] = {}
    for result in results:
        homographies[result.pair_id] = np.asarray(result.homography_matrix, dtype=np.float32)
    return homographies


def _run_stage_with_status(manager: ConfigManager, stage_name: str, func) -> Any:
    _update_stage_manifest(manager, stage_name, "running")
    try:
        result = func()
    except Exception as exc:
        _update_stage_manifest(manager, stage_name, "failed", error=str(exc))
        raise
    _update_stage_manifest(manager, stage_name, "completed")
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="双光谱热红外与可见光三维点云生成程序 - 全链路端到端执行")
    parser.add_argument(
        "command",
        nargs="?",
        default="run_all",
        choices=["run_all"],
        help="仅支持 run_all；可省略，默认执行 run_all",
    )
    parser.add_argument("--config-file", type=str, required=False, help="Path to JSON config file or workspace root")
    return parser


def run_all(args: argparse.Namespace) -> None:
    # 全链路串联，依赖 config_manager 处理统一配置
    print(f"Starting E2E pipeline with config source: {args.config_file}")
    manager = ConfigManager(user_config=args.config_file)

    stages = set(manager.runtime_config.stage_selection)
    print(f"Stages to execute: {sorted(stages)}")
    print(f"Output Root: {manager.run_dir}")

    workspace_root = Path(manager.workspace_root)
    dataset_root = Path(manager.runtime_config.input_dataset_root)
    calibration_root = Path(manager.runtime_config.calibration_dataset_root)
    rgb_dir = Path(manager.runtime_config.data_dir_rgb)
    thermal_tiff_dir = Path(manager.runtime_config.data_dir_thermal_tiff)
    thermal_preview_dir = dataset_root / "thermal_dir"
    if not thermal_preview_dir.exists():
        thermal_preview_dir = thermal_tiff_dir

    calibration_rgb_dir = calibration_root / "RGB"
    calibration_tir_dir = calibration_root / "NIR"

    calibration_bundle_path = Path(manager.get_calibration_output_path())
    undistorted_root = Path(manager.get_stage_dir("undistorted"))
    undistorted_rgb_dir = undistorted_root / "rgb"
    undistorted_tir_dir = undistorted_root / "thermal"
    undistorted_rgb_dir.mkdir(parents=True, exist_ok=True)
    undistorted_tir_dir.mkdir(parents=True, exist_ok=True)

    calibration_result = None
    undistorted_rgb_paths: List[Path] = []
    undistorted_tir_paths: List[Path] = []
    matching_results = []
    radiometry_results = []
    point_cloud = None
    reprojection_data = {}
    enriched_point_cloud = None
    reconstruction_image_sizes: Dict[str, Tuple[int, int]] = {}

    if "config" in stages:
        _update_stage_manifest(manager, "config", "completed", workspace_root=str(workspace_root))

    if "calibration" in stages:
        def _run_calibration() -> Dict[str, Any]:
            calibrator = DualSpectralCalibrator(config=manager)
            rgb_images = _find_sensor_images(calibration_rgb_dir, "RGB")
            tir_images = _find_sensor_images(calibration_tir_dir, "NIR")
            if not rgb_images:
                raise FileNotFoundError(f"No RGB calibration images found under {calibration_rgb_dir}")
            if not tir_images:
                raise FileNotFoundError(f"No thermal calibration images found under {calibration_tir_dir}")

            rgb_result = calibrator.calibrate([str(path) for path in rgb_images], sensor_name="RGB", is_tir=False)
            tir_result = calibrator.calibrate([str(path) for path in tir_images], sensor_name="NIR", is_tir=True)

            if not tir_result.get("focal_length_px") or len(tir_result.get("focal_length_px", [])) < 2:
                fallback_tir = _load_initial_calibration_xml(Path(manager.workspace_root) / "H30T_NIR.xml", "NIR")
                fallback_tir["source_images"] = [path.name for path in tir_images]
                tir_result = fallback_tir

            calibrator.save_calibration(rgb_result, manager)
            calibrator.save_calibration(tir_result, manager)
            return {"RGB": rgb_result, "NIR": tir_result}

        calibration_result = _run_stage_with_status(manager, "calibration", _run_calibration)

    if "preprocess" in stages:
        def _run_preprocess() -> Dict[str, List[str]]:
            if calibration_result is None:
                raise RuntimeError("Calibration results are required before preprocessing")

            calibration_bundle = _load_calibration_bundle(calibration_bundle_path)
            sensors = calibration_bundle.get("sensors", {})
            rgb_calibration = sensors.get("RGB")
            tir_calibration = sensors.get("NIR")
            if not rgb_calibration or not tir_calibration:
                raise RuntimeError("Both RGB and NIR calibration records are required")

            rgb_undistorter = ImageUndistorter(rgb_calibration)
            tir_undistorter = ImageUndistorter(tir_calibration)

            rgb_sources = _collect_images(rgb_dir, {".jpg", ".jpeg", ".png"})
            tir_sources = _collect_images(thermal_tiff_dir, {".tif", ".tiff"})
            if not rgb_sources:
                raise FileNotFoundError(f"No RGB images found under {rgb_dir}")
            if not tir_sources:
                raise FileNotFoundError(f"No thermal TIFF images found under {thermal_tiff_dir}")

            rgb_outputs: List[str] = []
            tir_outputs: List[str] = []
            for source_path in rgb_sources:
                output_path = _build_undistorted_path(undistorted_rgb_dir, source_path, False)
                rgb_undistorter.process_image(str(source_path), str(output_path), is_tir=False)
                rgb_outputs.append(str(Path(output_path).with_suffix(".png")))

            for source_path in tir_sources:
                output_path = _build_undistorted_path(undistorted_tir_dir, source_path, True)
                tir_undistorter.process_image(str(source_path), str(output_path), is_tir=True)
                tir_outputs.append(str(Path(output_path).with_suffix(".tiff")))

            return {"rgb": rgb_outputs, "thermal": tir_outputs}

        preprocess_result = _run_stage_with_status(manager, "preprocess", _run_preprocess)
        undistorted_rgb_paths = [Path(path) for path in preprocess_result["rgb"]]
        undistorted_tir_paths = [Path(path) for path in preprocess_result["thermal"]]

    if "matching" in stages:
        def _run_matching_stage():
            twmm_params = _prepare_twmm_params(manager)
            adapter = TwmmAdapter(workspace_root=Path(manager.workspace_root), twmm_root=workspace_root / "TWMM-main")
            return adapter.match_batch_from_dirs(
                rgb_dir=undistorted_rgb_dir,
                thermal_tiff_dir=undistorted_tir_dir,
                thermal_preview_dir=thermal_preview_dir,
                params=twmm_params,
                output_dir=Path(manager.get_stage_dir("matching")),
                max_pairs=manager.user_config.get("max_matching_pairs"),
            )

        matching_results = _run_stage_with_status(manager, "matching", _run_matching_stage)

    if "radiometry" in stages:
        def _run_radiometry_stage():
            radiometry_params = _prepare_radiometry_params(manager)
            env = _prepare_environment(manager)
            pipeline = RadiometryPipeline(workspace_root=Path(manager.workspace_root), params=radiometry_params)
            return pipeline.process_batch(
                thermal_tiff_dir=thermal_tiff_dir,
                env=env,
                output_dir=Path(manager.get_stage_dir("radiometry")),
                max_frames=manager.user_config.get("max_radiometry_frames"),
            )

        radiometry_results = _run_stage_with_status(manager, "radiometry", _run_radiometry_stage)

    if "metashape" in stages:
        def _run_metashape_stage():
            try:
                from Metashape.photogrammetry import PhotogrammetryEngine
            except Exception as exc:
                raise RuntimeError(f"Metashape runtime is not available: {exc}") from exc

            images = _collect_images(undistorted_rgb_dir, {".png", ".jpg", ".jpeg"})
            if not images:
                images = _collect_images(rgb_dir, {".jpg", ".jpeg", ".png"})
            if not images:
                raise FileNotFoundError("No RGB images available for Metashape reconstruction")

            engine_cfg = manager.user_config.get("photogrammetry", {}) if hasattr(manager, "user_config") else {}
            engine = PhotogrammetryEngine({"photogrammetry": engine_cfg})
            image_arrays = []
            nonlocal reconstruction_image_sizes
            reconstruction_image_sizes = {}
            for image_path in images:
                import cv2

                image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
                if image is not None:
                    image_arrays.append(image)
                    reconstruction_image_sizes[image_path.stem] = (int(image.shape[1]), int(image.shape[0]))

            if not image_arrays:
                raise FileNotFoundError("Unable to read RGB inputs for Metashape reconstruction")

            return engine.reconstruct_point_cloud(image_arrays, [path.stem for path in images[: len(image_arrays)]])

        point_cloud = _run_stage_with_status(manager, "metashape", _run_metashape_stage)

    if "geometry" in stages:
        def _run_geometry_stage():
            if point_cloud is None:
                raise RuntimeError("Metashape point cloud is required before reprojection")

            camera_params: Dict[str, Dict[str, Any]] = {}
            for camera_id, pose in point_cloud.camera_poses.items():
                intrinsics = point_cloud.intrinsics.get(camera_id, np.eye(3, dtype=np.float32))
                width, height = reconstruction_image_sizes.get(
                    camera_id,
                    (int(manager.dataset_profile.rgb_resolution[0]), int(manager.dataset_profile.rgb_resolution[1])),
                )
                camera_params[camera_id] = {
                    "K": np.asarray(intrinsics, dtype=np.float32),
                    "pose": np.asarray(pose, dtype=np.float32),
                    "width": width,
                    "height": height,
                }

            exporter = ReprojectionExporter(point_cloud=point_cloud, camera_params=camera_params)
            return exporter.export_reprojections()

        reprojection_data = _run_stage_with_status(manager, "geometry", _run_geometry_stage)

    if "enrichment" in stages:
        def _run_enrichment_stage():
            if point_cloud is None:
                raise RuntimeError("Point cloud is required before enrichment")
            if not matching_results:
                raise RuntimeError("Matching results are required before enrichment")
            if not radiometry_results:
                raise RuntimeError("Radiometry results are required before enrichment")

            thermal_maps = _load_radiometry_temperature_maps(radiometry_results)
            homography_maps = _load_matching_homographies(matching_results)
            if not thermal_maps:
                raise RuntimeError("No thermal maps available for enrichment")

            enricher = ThermalEnricher(homography=homography_maps, thermal_data=thermal_maps, config={"thermal_extraction": {"thermal_resolution": "1280x1024"}})
            return enricher.enrich_point_cloud(point_cloud, reprojection_data)

        enriched_point_cloud = _run_stage_with_status(manager, "enrichment", _run_enrichment_stage)

    if "validation" in stages:
        def _run_validation_stage():
            if enriched_point_cloud is None:
                raise RuntimeError("Enriched point cloud is required before export")
            output_path = Path(manager.get_stage_dir("validation")) / f"thermal_point_cloud_{manager.run_id}.ply"
            export_thermal_point_cloud_ply(enriched_point_cloud, str(output_path))
            return {"ply_path": str(output_path)}

        _run_stage_with_status(manager, "validation", _run_validation_stage)

    print("Pipeline execution completed.")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # 仅保留 run_all 入口；command 参数仅用于兼容 `python main.py run_all`
    run_all(args)


if __name__ == "__main__":
    main()
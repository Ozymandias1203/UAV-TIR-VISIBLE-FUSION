"""
photogrammetry.py
Step 6: Photogrammetric Reconstruction using Agisoft Metashape 2.2.1 API.
"""
import os
import tempfile
import numpy as np
import cv2
from typing import List, Dict, Any

try:
    import Metashape
except ImportError as e:
    print("Agisoft Metashape is not available. Please run within Metashape Python environment.")
    raise e

class PointCloud:

    def __init__(self, points: np.ndarray, colors: np.ndarray, normals: np.ndarray,
                 camera_poses: Dict[str, np.ndarray], intrinsics: Dict[str, np.ndarray]) -> None:
        self.points = points            
        self.colors = colors            
        self.normals = normals          
        self.camera_poses = camera_poses  
        self.intrinsics = intrinsics      

class PhotogrammetryEngine:
    def __init__(self, config: Dict[str, Any]) -> None:
        photogrammetry_config = config.get("photogrammetry", {})
        self.engine = photogrammetry_config.get("engine", "Metashape")
        flight_config = photogrammetry_config.get("flight_parameters", {})
        self.downscale_align = int(flight_config.get("downscale_align", 1)) # 1=High
        self.downscale_depth = int(flight_config.get("downscale_depth", 4)) # 4=Medium
        print(f"PhotogrammetryEngine initialized. Engine: {self.engine}")

    def reconstruct_point_cloud(self, images: List[np.ndarray], image_labels: List[str] = None) -> PointCloud:
        if not images:
            raise ValueError("No images provided for reconstruction.")

        with tempfile.TemporaryDirectory() as temp_dir:
            image_file_paths = []
            for idx, img in enumerate(images):
                filename = os.path.join(temp_dir, f"img_{idx:04d}.jpg" if not image_labels else f"{image_labels[idx]}.jpg")
                cv2.imwrite(filename, img)
                image_file_paths.append(filename)

            doc = Metashape.Document()
            chunk = doc.addChunk()
            chunk.addPhotos(image_file_paths)
            print(f"Added {len(image_file_paths)} RGB photos to chunk.")

            try:
                # SfM Alignment
                chunk.matchPhotos(downscale=self.downscale_align, generic_preselection=True, reference_preselection=False)
                chunk.alignCameras()
                chunk.optimizeCameras()
                print("Image alignment and camera optimization completed.")

                # MVS Dense Reconstruction (Metashape 2.2.1 uses buildDepthMaps & buildPointCloud)
                chunk.buildDepthMaps(downscale=self.downscale_depth, filter_mode=Metashape.MildFiltering)
                chunk.buildPointCloud(source_data=Metashape.DepthMapsData)
                print("Dense point cloud construction completed.")
            except Exception as e:
                raise RuntimeError(f"Metashape processing error: {e}")

            # Extract points (Metashape 2.2.1 attribute for dense cloud is chunk.point_cloud)
            dense_cloud = chunk.point_cloud
            if dense_cloud is None or len(dense_cloud.points) == 0:
                raise RuntimeError("Dense reconstruction failed: No points in the point cloud.")

            points_list, colors_list, normals_list = [], [], []
            for pt in dense_cloud.points:
                if pt.valid:
                    points_list.append(np.array(pt.coord, dtype=np.float32))
                    colors_list.append(np.array(pt.color, dtype=np.uint8) if pt.color else np.array([128,128,128]))
                    normals_list.append(np.array(pt.normal, dtype=np.float32) if pt.normal else np.array([0,0,1]))

            points_np = np.vstack(points_list)
            colors_np = np.vstack(colors_list)
            normals_np = np.vstack(normals_list)
            print(f"Extracted {points_np.shape[0]} valid 3D points.")

            camera_poses, intrinsics = {}, {}
            for cam in chunk.cameras:
                if not cam.transform:
                    continue
                cam_label = cam.label.split('.')[0]
                camera_poses[cam_label] = np.array(cam.transform, dtype=np.float32)
                
                # Retrieve Calibration matrix (K)
                if cam.sensor and cam.sensor.calibration:
                    calib = cam.sensor.calibration
                    intrinsics[cam_label] = np.array([[calib.f, calib.b1, calib.cx + calib.width/2],
                                                      [0,      calib.f,  calib.cy + calib.height/2],
                                                      [0,      0,       1]], dtype=np.float32)
                else:
                    intrinsics[cam_label] = np.eye(3, dtype=np.float32)

            return PointCloud(points=points_np, colors=colors_np, normals=normals_np,
                              camera_poses=camera_poses, intrinsics=intrinsics)
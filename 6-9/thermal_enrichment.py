"""
thermal_enrichment.py
Step 8: Thermal Enrichment. 
Applies Homography to project RGB pixels to Thermal domain. Fuses temperatures based on view distance weights.
"""
import numpy as np
import logging
from typing import Dict, Any

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class ThermalEnricher:
    def __init__(self, homography: np.ndarray, thermal_data: Dict[str, np.ndarray], config: Dict[str, Any]) -> None:
        self.homography = homography
        # For RGB to Thermal, we directly multiply by homography H
        self.thermal_data = thermal_data
        thermal_res_str = config.get("thermal_extraction", {}).get("thermal_resolution", "1280x1024")
        w_str, h_str = thermal_res_str.split("x")
        self.thermal_w, self.thermal_h = int(w_str), int(h_str)
        self.epsilon = 1e-4

    def bilinear_interpolate(self, img: np.ndarray, u: float, v: float) -> float:
        u0, v0 = int(np.floor(u)), int(np.floor(v))
        u1, v1 = min(u0 + 1, img.shape[1] - 1), min(v0 + 1, img.shape[0] - 1)
        
        alpha, beta = u - u0, v - v0
        T00, T10 = img[v0, u0], img[v0, u1]
        T01, T11 = img[v1, u0], img[v1, u1]
        
        # If thermal missing values are represented as NaN, ignore
        if np.isnan([T00, T10, T01, T11]).any():
            return np.nan
            
        return (1 - alpha) * (1 - beta) * T00 + alpha * (1 - beta) * T10 + \
               (1 - alpha) * beta * T01 + alpha * beta * T11

    def enrich_point_cloud(self, point_cloud: Any, reprojection_data: Dict[str, list]) -> Any:
        num_points = point_cloud.points.shape[0]
        temperatures = np.full((num_points,), np.nan, dtype=np.float32)
        support_views = np.zeros((num_points,), dtype=np.int32)
        fusion_weights = np.zeros((num_points,), dtype=np.float32)

        valid_count = 0
        for idx in range(num_points):
            point_id = f"point_{idx:06d}"
            observations = reprojection_data.get(point_id, [])
            
            t_sum, w_sum, views = 0.0, 0.0, 0
            for obs in observations:
                cam_id = obs.get("camera_id")
                # Corresponding thermal image ID logic based on dataset_profile
                thermal_cam_id = cam_id.replace("RGB", "NIR") if "RGB" in cam_id else cam_id 
                thermal_img = self.thermal_data.get(thermal_cam_id)
                
                if thermal_img is None:
                    continue

                # Map RGB (x, y) to Thermal (u, v) using Homography
                rgb_h = np.array([obs["x"], obs["y"], 1.0], dtype=np.float32)
                th_h = self.homography @ rgb_h
                u, v = th_h[0] / th_h[2], th_h[1] / th_h[2]

                if 0 <= u < self.thermal_w and 0 <= v < self.thermal_h:
                    temp_val = self.bilinear_interpolate(thermal_img, u, v)
                    if not np.isnan(temp_val):
                        # Weight strategy: inversly proportional to distance from optical center
                        dist_to_center = obs.get("distance_to_center", self.epsilon)
                        weight = 1.0 / (dist_to_center + self.epsilon)
                        
                        t_sum += weight * temp_val
                        w_sum += weight
                        views += 1
            
            if views > 0:
                temperatures[idx] = t_sum / w_sum
                support_views[idx] = views
                fusion_weights[idx] = w_sum
                valid_count += 1

        logging.info(f"Thermal enrichment complete. Coverage: {(valid_count/num_points)*100:.2f}%")
        point_cloud.temperature = temperatures
        point_cloud.support_views = support_views
        point_cloud.fusion_weights = fusion_weights
        return point_cloud
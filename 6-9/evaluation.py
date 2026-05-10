"""
evaluation.py (Export snippet extension)
Step 9: Quality Export and Serialization.
"""
import os
import logging
import numpy as np

def export_thermal_point_cloud_ply(point_cloud, output_path: str):
    """
    Exports the enriched point cloud to a standard PLY format.
    Fields comply with file_formats.md contract:
    x, y, z, r, g, b, temperature, support_view_count, fusion_weight
    """
    points = point_cloud.points
    colors = point_cloud.colors
    temps = point_cloud.temperature
    views = point_cloud.support_views
    weights = point_cloud.fusion_weights
    
    num_points = points.shape[0]
    
    try:
        with open(output_path, "w") as f:
            f.write("ply\n")
            f.write("format ascii 1.0\n")
            f.write(f"element vertex {num_points}\n")
            f.write("property float x\nproperty float y\nproperty float z\n")
            f.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
            f.write("property float temperature\n")
            f.write("property int support_view_count\n")
            f.write("property float fusion_weight\n")
            f.write("end_header\n")
            
            for i in range(num_points):
                x, y, z = points[i]
                r, g, b = colors[i]
                temp = temps[i]
                view_c = views[i]
                weight = weights[i]
                
                # Explicit NaN marker for missing temperature data
                temp_str = f"{temp:.4f}" if not np.isnan(temp) else "NaN"
                
                f.write(f"{x:.4f} {y:.4f} {z:.4f} {r} {g} {b} {temp_str} {view_c} {weight:.4f}\n")
                
        logging.info(f"Successfully exported thermally enriched point cloud to {output_path}")
    except Exception as e:
        logging.error(f"Failed to export PLY: {e}")
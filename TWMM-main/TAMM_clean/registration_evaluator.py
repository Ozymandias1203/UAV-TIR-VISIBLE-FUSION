import os
import cv2
import numpy as np
import time
import shutil

# 注意：需要保证下面这些模块在项目中可以导入（例如TAMM算法、特征提取、异常点过滤、单应性计算等）
from thermal_visible import ThermalVisble
from image_process import filter_outliers, findHomoraphy


def one_img_process(therma_img_path, therma_rgb_path, visible_img_path, out_img, kwargs):
    
    kwargs = {
        'thermal_upsample': 2.3,
        'patch_size': 32,
        'search_radius': 16,
        'level_max': 3,
        'scale': {'thermal': 1.0, 'visible': 2.3},
        'crop_size': (640,512),
        'score_weights': {
            'w_rcp': 1.1,  # 【w_rcp】 RCP权重，可手动调整（支持负数）
            'w_mse': -0.2  # 【w_mse】 MSE权重，可手动调整（支持负数）
        },
        'mse_threshold': 170  # 初始设定的MSE阈值，用于归一化MSE得分
    }

    thermal_visible_c = ThermalVisble(
        thermal_tiff_path=therma_img_path,
        thermal_rgb_path=therma_rgb_path,
        visible_rgb_path=visible_img_path,
        scale=kwargs['thermal_upsample'],
        crop_size=kwargs['crop_size'],
        attention_flag=False
    )
    

    s1 = time.time()
    # 提取CFOG特征
    thermal_visible_c.get_img_features(method='CFOG', bin_size=9)
    # CFOG TAMM配准
    corres = thermal_visible_c.get_correspoints(method='TAMM',
                                                patch_size=kwargs['patch_size'],
                                                search_radius=kwargs['search_radius'],
                                                level_max=kwargs['level_max'])
    s2 = time.time()
    print(s2 - s1)


    # 根据corres的实际数据类型调整访问方式
    if isinstance(corres, dict):
        # 假设corres是一个字典，我们需要迭代它的键值对
        thermal_list = []
        visible_list = []
        for key, value in corres.items():
            thermal_list.append(key)
            visible_list.append(value[0])  # 假设value是一个元组，且第一个元素是可见图像中的点
    else:
        print("corres is not a dictionary. Skipping further processing.")
        return None

    # filer points
    thermal_point_list, sen_point_list, outliers_ref, outliers_sen = filter_outliers(thermal_list, visible_list, thresh=2, method='NBCS')
    good = [[_thermal[1], _thermal[0], _sen[1], _sen[0]] for (_thermal, _sen) in zip(thermal_point_list, sen_point_list)]
    H_mat, flag_result = findHomoraphy(good)

    # 做warp
    thermal_visible_c.homo_warp(H_mat)
    thermal_visible_c.draw_matchpoints(thermal_point_list, sen_point_list, outliers_ref, outliers_sen)

    # 结果保存
    save_dict = {
        'corres_points': 'csv',
        'homo': 'csv',
        'matchpoints_img': 'jpg',
        'visible_rgb': 'png',
        'thermal_rgb': 'png',
        'warp_thermal': 'png',
        'mosaic': 'png'
    }
    thermal_visible_c.result_save(out_img, save_dict)
    return H_mat


def evaluate_registration(thermal_tiff_path, thermal_rgb_path, visible_img_path, kwargs):
    """
    对一张可见光影像与热红外影像进行配准，并计算：
      - TCP: 总控制点数（即get_correspoints返回的匹配点数量）
      - CCP: 过滤异常后的正确匹配点数
      - RCP: 正确匹配率（百分比）
      - MSE: 根据计算的单应矩阵，对匹配点进行变换后与热红外图对应点之间的均方误差
    同时返回计算得到的单应矩阵 H（若配准失败则返回None）。
    """
    # 构造热红外与可见光的配准对象
    thermal_visible_c = ThermalVisble(
        thermal_tiff_path=thermal_tiff_path,
        thermal_rgb_path=thermal_rgb_path,
        visible_rgb_path=visible_img_path,
        scale=kwargs['thermal_upsample'],
        crop_size=kwargs['crop_size'],
        attention_flag=False
    )

    # 提取特征
    thermal_visible_c.get_img_features(method='CFOG', bin_size=9)

    # 使用TAMM算法获得匹配点（这里返回一个字典）
    corres = thermal_visible_c.get_correspoints(
        method='TAMM',
        patch_size=kwargs['patch_size'],
        search_radius=kwargs['search_radius'],
        level_max=kwargs['level_max']
    )

    # 如果返回结果为字典，则计算总控制点数 TCP
    if isinstance(corres, dict):
        TCP = len(corres)
        thermal_points = []
        visible_points = []
        for key, value in corres.items():
            thermal_points.append(key)
            visible_points.append(value[0])  # 假设第一个元素为可见图像中的匹配点
    else:
        print("匹配结果不是字典格式，跳过该图像。")
        return None

    # 过滤异常点，得到经过验证的匹配点 CCP
    thermal_point_list, visible_point_list, outliers_ref, outliers_vis = filter_outliers(
        thermal_points, visible_points, thresh=2, method='NBCS'
    )
    CCP = len(thermal_point_list)
    RCP = (CCP / TCP * 100) if TCP > 0 else 0

    # 利用过滤后的匹配点计算单应矩阵 H
    # 构造匹配点列表，注意这里坐标顺序可能需要与findHomoraphy一致（例如 [x_thermal, y_thermal, x_visible, y_visible]）
    good = [[pt_t[1], pt_t[0], pt_v[1], pt_v[0]] for pt_t, pt_v in zip(thermal_point_list, visible_point_list)]
    H, flag = findHomoraphy(good)

    # 计算均方误差 MSE：将可见图像匹配点用 H 变换到热红外图中，与热红外匹配点的距离
    if H is None:
        mse = np.inf
    else:
        visible_pts_np = np.float32(visible_point_list).reshape(-1, 1, 2)
        warped_visible = cv2.perspectiveTransform(visible_pts_np, H).reshape(-1, 2)
        thermal_pts_np = np.float32(thermal_point_list)
        errors = np.linalg.norm(warped_visible - thermal_pts_np, axis=1) ** 2
        mse = np.mean(errors)

    return TCP, CCP, RCP, mse, H


def process_frame(frame_dir, data_root=r"D:\pycharm\TWMM-main\data", kwargs=None, mse_values=None):
    """
    处理单个帧的配准任务，并输出配准效果最好的原始可见光影像。
    参数 mse_values 为列表，用于收集所有被比较（MSE < 1500）的图像的 MSE 值。
    """
    thermal_dir = os.path.join(data_root, "thermal", frame_dir)  # 热红外图像目录
    visible_dir = os.path.join(data_root, "visible", frame_dir)  # 可见光图像目录
    output_dir = os.path.join(data_root, "output", frame_dir)  # 输出目录

    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    # 构建热红外文件路径
    thermal_tiff_path = os.path.join(thermal_dir, f"{frame_dir}.tiff")  # 热红外 TIFF 文件路径
    thermal_rgb_path = os.path.join(thermal_dir, f"{frame_dir}.jpg")  # 热红外 RGB 文件路径

    # 检查文件是否存在
    if not (os.path.exists(thermal_tiff_path) and os.path.exists(thermal_rgb_path)):
        print(f"跳过帧 {frame_dir}: 热红外文件缺失")
        return

    best_visible = None
    best_score = -np.inf
    best_fname = None

    # 评分参数和 MSE 阈值（用于归一化MSE得分，满分为100）
    score_params = kwargs.get('score_weights', {'w_rcp': 1.4, 'w_mse': -0.4})
    w_rcp = score_params['w_rcp']  # 【w_rcp】
    w_mse = score_params['w_mse']  # 【w_mse】
    mse_threshold = kwargs.get('mse_threshold', 50)  # 初始阈值，后续可根据数据集分布更新

    # 遍历所有可见光图像
    # 存储所有图像的评分
    scored_images = []
    
    for fname in sorted(os.listdir(visible_dir)):
        if fname.lower().endswith(('.jpg', '.png', '.jpeg')):
            visible_img_path = os.path.join(visible_dir, fname)
            metrics = evaluate_registration(thermal_tiff_path, thermal_rgb_path, visible_img_path, kwargs)
            if metrics is None:
                continue
            TCP, CCP, RCP, mse, H = metrics

            if mse > 3000:
                print(f"帧 {frame_dir} - {fname}: MSE={mse:.2f} 超过3000，跳过")
                continue

            if mse_values is not None:
                mse_values.append(mse)

            mse_score = max(-1, (mse_threshold - mse) / mse_threshold) * 100
            score = w_rcp * RCP + w_mse * mse_score

            print(f"帧 {frame_dir} - {fname}: TCP={TCP}, CCP={CCP}, RCP={RCP:.2f}%, MSE={mse:.2f}, 得分={score:.2f}")
            
            scored_images.append((score, visible_img_path, fname))
    
    # 按得分降序排列
    scored_images.sort(reverse=True, key=lambda x: x[0])
    
    if len(scored_images) < 1:
        print(f"帧 {frame_dir} 可选影像少于2个，无法选择第二高得分影像")
        return

    # 选择第最高的影像
    third_best_score, best_visible, best_fname = scored_images[0]
    print(f"帧 {frame_dir} 选择评分第一高影像: {best_fname}，得分为: {third_best_score:.2f}")
    
    output_path = os.path.join(output_dir, "best_registered.jpg")
    shutil.copy(best_visible, output_path)
    print(f"帧 {frame_dir} 评分第一高的影像已复制至: {output_path}")
    
    one_img_process(thermal_tiff_path, thermal_rgb_path, output_path, output_dir, kwargs)
    

def main():
    # 数据集根目录（根据实际情况修改）
    data_root = r"C:\Users\16854\Desktop\evenving-data-res\part4"

    # 配准参数
    kwargs = {
        'thermal_upsample': 2.3,
        'patch_size': 32,
        'search_radius': 16,
        'level_max': 3,
        'scale': {'thermal': 1.0, 'visible': 2.3},
        'crop_size': (640,512),
        'score_weights': {
            'w_rcp': 1.0,  # 【w_rcp】 RCP权重，可手动调整（支持负数）
            'w_mse': 0.3  # 【w_mse】 MSE权重，可手动调整（支持负数）
        },
        'mse_threshold': 170  # 初始设定的MSE阈值，用于归一化MSE得分
    }

    # 用于收集整个数据集中所有有效（MSE < 1500）的图像的 MSE 值
    all_mse_values = []

    # 获取所有帧目录（假设thermal目录下的子目录名为帧编号）
    thermal_frames = [d for d in os.listdir(os.path.join(data_root, "thermal"))
                      if os.path.isdir(os.path.join(data_root, "thermal", d))]
    # 遍历所有帧
    for frame_dir in sorted(thermal_frames):
        print(f"\n处理帧: {frame_dir}")
        process_frame(frame_dir, data_root=data_root, kwargs=kwargs, mse_values=all_mse_values)

    # 数据集配准结束后，计算所有有效图像的 MSE 分布
    if len(all_mse_values) > 0:
        mse_array = np.array(all_mse_values)
        mean_mse = np.mean(mse_array)
        std_mse = np.std(mse_array)
        # 重新设定 mse_threshold 为 平均 MSE + 1 标准差
        new_mse_threshold = mean_mse + std_mse
        print("\n数据集 MSE 分布统计：")
        print(f"有效图像数量：{len(all_mse_values)}")
        print(f"平均 MSE: {mean_mse:.2f}")
        print(f"标准差: {std_mse:.2f}")
        print(f"建议的 mse_threshold（平均 MSE + 1 标准差）: {new_mse_threshold:.2f}")
    else:
        print("未收集到有效的 MSE 数据，无法计算数据集分布。")




if __name__ == '__main__':
    main()


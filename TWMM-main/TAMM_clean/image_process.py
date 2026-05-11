import rasterio 
from rasterio.enums import Resampling
import cv2
import os
import numpy as np
import scipy.io as sio
from tools import csv_to_list,exist_or_mkdir

def fusion_image(thermal_img,rgb_img):
    #来自H:\mlx_thermal_Images\code\CFOG_testSCBTemplate2\test_fusion
    W,H,C=thermal_img.shape
    weight=np.zeros([W,H,3]).astype(np.uint8)
    for m in range(W):
        weight[m]=1 if int(m/100)%2==0 else 0

    for n in range(H):
        weight[:,n]=1-weight[:,n] if int(n/100)%2==1 else weight[:,n]

    alpha=0.5#颜色融合
    alpha=weight#镶嵌融合
    fusion=alpha*thermal_img+(1-alpha)*rgb_img
    fusion=fusion.astype(np.uint8)
    return fusion

def stitch_img(imgList,hor_flag=1,outpath=None,spacing=10):
    """
    stich images in imgList horizontal
    hor_flag:1 horizontal stitch else vertical stitch
    """
    if len(imgList[0].shape)==2:
        imgList=[_[:,:,np.newaxis] for _ in imgList]
    channels=[_.shape[2] for _ in imgList]
    assert (np.array(channels)==3).all() or (np.array(channels)==1).all()
    hor_shape=[_.shape[1] for _ in imgList]
    ver_shape=[_.shape[0] for _ in imgList]
    hor=np.sum(hor_shape)+spacing*(len(imgList)-1) if hor_flag==1 else np.max(hor_shape)
    ver=np.max(ver_shape) if hor_flag==1 else np.sum(ver_shape)+spacing*(len(imgList)-1)
    stitch=np.ones((ver,hor,3)).astype(np.uint8)*255
    for num,img in enumerate(imgList):
        ver_start=0 if hor_flag==1 else np.sum(ver_shape[0:num])+spacing*(num)
        ver_end=img.shape[0] if hor_flag==1 else np.sum(ver_shape[0:num+1])+spacing*(num)
        hor_start=np.sum(hor_shape[0:num])+spacing*(num) if hor_flag==1 else 0
        hor_end=np.sum(hor_shape[0:num+1])+spacing*(num) if hor_flag==1 else img.shape[1]
        stitch[int(ver_start):int(ver_end),int(hor_start):int(hor_end)]=img
    if outpath:
        exist_or_mkdir(os.path.split(outpath)[0])
        cv2.imwrite(outpath,stitch)
    return stitch

def img_read(img_path,scale):
    """
    img_path:path of img
    scale：
    grey flag：
    """
    if not os.path.exists(img_path):
        raise FileNotFoundError(f"图像文件不存在: {img_path}")
    
    file_ext = os.path.split(img_path)[1].split('.')[-1].lower()
    
    #if end with '.tiff' or '.tif'
    if file_ext in ['mat']:
        img=sio.loadmat(img_path)['img1_scb']
        if scale!=1:
            img=cv2.resize(img,(int(img.shape[1]*scale),int(img.shape[0]*scale)))

    elif file_ext in ['tiff','tif']:
        try:
            with rasterio.open(img_path) as dataset:
                if scale != 1:
                    img = dataset.read(out_shape=(dataset.count, int(dataset.height * scale),
                                                      int(dataset.width * scale)), resampling=Resampling.bilinear)
                else:
                    img=dataset.read()
                img=img[0]
        except Exception as e:
            raise RuntimeError(f"无法读取TIFF文件 {img_path}: {str(e)}")
    else:
        # if end with '.png' or '.jpg'
        img=cv2.imread(img_path)
        if img is None:
            raise RuntimeError(f"无法读取图像文件 {img_path}，请检查文件格式是否正确")
        if scale!=1:
            img=cv2.resize(img,(int(img.shape[1]*scale),int(img.shape[0]*scale)))
    
    if img is None:
        raise RuntimeError(f"读取图像失败，返回值为None: {img_path}")
    
    return img



# def center_crop(img,crop_size=1000):
#     if len(img.shape)==3:
#         if img.shape[0]==1:
#             c,m,n=img.shape
#         else:
#             m, n,c = img.shape
#     else:
#         m,n=img.shape
#     s_m = (m - crop_size) / 2
#     s_n = (n - crop_size) / 2
#     B = [int(s_m), int(s_n), crop_size, crop_size]
#     if img.shape[0]==1:
#         img = img[0,B[0]:B[0] + B[2], B[1]:B[1] + B[3]]
#     else:
#         img = img[B[0]:B[0] + B[2], B[1]:B[1] + B[3]]
#     return img

def center_crop(img, crop_size=1000):
    # 检查输入是否为None
    if img is None:
        raise ValueError("输入图像为None，无法进行裁剪。请检查图像文件是否正确读取。")
    
    if isinstance(crop_size, int):
        crop_width, crop_height = crop_size, crop_size
    elif isinstance(crop_size, tuple) and len(crop_size) == 2:
        crop_width, crop_height = crop_size
    else:
        raise ValueError("crop_size must be an integer or a tuple of two integers")

    if len(img.shape) == 3:
        if img.shape[0] == 1:
            c, m, n = img.shape
        else:
            m, n, c = img.shape
    else:
        m, n = img.shape

    s_m = (m - crop_height) // 2
    s_n = (n - crop_width) // 2

    B = [int(s_m), int(s_n), crop_height, crop_width]
    if img.shape[0] == 1:
        img = img[0, B[0]:B[0] + B[2], B[1]:B[1] + B[3]]
    else:
        img = img[B[0]:B[0] + B[2], B[1]:B[1] + B[3]]

    return img

def bottom_right_crop(img, crop_size=1000, offset=(0, 0)):
    """
    从右下角裁剪图像，支持偏移调整
    
    参数:
        img: 输入图像
        crop_size: 裁剪尺寸，可以是整数（正方形）或元组(width, height)
        offset: 裁剪区域偏移量，元组(offset_x, offset_y)
                offset_x > 0: 向左移动，offset_x < 0: 向右移动
                offset_y > 0: 向上移动，offset_y < 0: 向下移动
    
    返回:
        裁剪后的图像
    """
    # 检查输入是否为None
    if img is None:
        raise ValueError("输入图像为None，无法进行裁剪。请检查图像文件是否正确读取。")
    
    if isinstance(crop_size, int):
        crop_width, crop_height = crop_size, crop_size
    elif isinstance(crop_size, tuple) and len(crop_size) == 2:
        crop_width, crop_height = crop_size
    else:
        raise ValueError("crop_size must be an integer or a tuple of two integers")

    # 解析偏移量
    if isinstance(offset, (int, float)):
        offset_x, offset_y = int(offset), 0
    elif isinstance(offset, (tuple, list)) and len(offset) == 2:
        offset_x, offset_y = int(offset[0]), int(offset[1])
    else:
        offset_x, offset_y = 0, 0

    # 获取图像尺寸
    if len(img.shape) == 3:
        if img.shape[0] == 1:
            c, m, n = img.shape
            channel_first = True
        else:
            m, n, c = img.shape
            channel_first = False
    else:
        m, n = img.shape
        channel_first = False

    # 确保裁剪尺寸不超过图像尺寸
    crop_width = min(crop_width, n)
    crop_height = min(crop_height, m)
    
    # 从右下角开始裁剪，然后应用偏移
    # 起始位置：右下角坐标减去裁剪尺寸，再减去偏移量
    start_n = n - crop_width - offset_x  # 列起始位置（从右边开始，offset_x>0向左移动）
    start_m = m - crop_height - offset_y  # 行起始位置（从下边开始，offset_y>0向上移动）
    
    # 确保裁剪区域在图像范围内
    start_n = max(0, min(start_n, n - crop_width))
    start_m = max(0, min(start_m, m - crop_height))
    
    # 执行裁剪
    if channel_first and img.shape[0] == 1:
        img = img[0, start_m:start_m + crop_height, start_n:start_n + crop_width]
    else:
        img = img[start_m:start_m + crop_height, start_n:start_n + crop_width]

    return img
def homo_save(csv_path,H):
    with open (csv_path,'w') as file:
        file.write(str(H[0][0])+' '+str(H[0][1])+' '+str(H[0][2])+'\n')
        file.write(str(H[1][0]) + ' ' + str(H[1][1]) + ' ' + str(H[1][2]) + '\n')
        file.write(str(H[2][0]) + ' ' + str(H[2][1]) + ' ' + str(H[2][2]) + '\n')



def homo_parse(homo_path):
    def str_f(str_homo):
        str_homo = str_homo[0].split(' ')
        return [float(_) for _ in str_homo]

    homography = np.zeros([3, 3])
    homo = csv_to_list(homo_path)
    homo = homo[0:3]
    homo = [str_f(_) for _ in homo]
    homo = np.array(homo)
    return homo


def img_split(img,patch_size,overlap=0):
    #目前只处理overlap=0的情况
    if overlap==0:
        num_index_x = int(img.shape[0] / patch_size)
        num_index_y = int(img.shape[1] / patch_size)
        patch_xy = []

        for index_x in range(num_index_x):
            patch_y = []
            for index_y in range(num_index_y):
                patch_y.append(img[index_x * patch_size:index_x * patch_size + patch_size,
                                           index_y * patch_size:index_y * patch_size + patch_size])
            patch_xy.append(patch_y)
    else:
        return
    return patch_xy,num_index_x,num_index_y

def SSD_template_matching(template,image,attention,return_score_flag):
    """
    return_score_flag:为True,表明返回score,越大表明配准效果越好；
    """
    ssd_error=np.zeros([image.shape[0]-template.shape[0]+1,image.shape[1]-template.shape[1]+1])
    if len(image.shape)==3:
        #遍历所有可能的位置，一一计算得分
        for index_x in range(ssd_error.shape[0]):
            for index_y in range(ssd_error.shape[1]):
                image_small=image[index_x:index_x+template.shape[0],index_y:index_y+template.shape[1]]
                ##Sum of Square Distance的方法计算得分
                error_pixel=(template-image_small)**2
                error_pixel=np.sum(error_pixel,axis=2)
                if attention is not None:
                    error_pixel=error_pixel*attention
                error_all=np.sum(error_pixel)
                ssd_error[index_x,index_y]=error_all

    # 归一化为0-1,max(=1) means max error,min(==0) means min error.
    matcherror = (ssd_error - np.min(ssd_error)) / (np.max(ssd_error) - np.min(ssd_error) + 1e-30)
    #translate to make max(==1) means best match
    if return_score_flag:
        matchscore = 1 - matcherror
        return matchscore
    else:
        return matcherror

def show_map_and_img(map_img,norm_flag,color_flag=True,cv_show_name=None,outpath=None):
    map_img=np.array(map_img)
    if norm_flag=='map':
        #归一化0-255
        map_0_255=cv2.normalize(map_img, dst=None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX).astype(np.uint8)
        map_color= cv2.applyColorMap(map_0_255, cv2.COLORMAP_JET)if color_flag else map_0_255
        img=map_color
    else:
        img=map_img
    if outpath:
        cv2.imwrite(outpath,img)
    if cv_show_name:
        cv2.imshow(cv_show_name,img)
    return img

def get_cores_point(pt_x,pt_y,homo):
    #pt_x,pt_y是按照array的先行后列的顺序
    _ = np.array([pt_y, pt_x, 1])
    _2 = np.dot(np.array(homo), (_.T))
    _2 = _2 / _2[2]
    #返回结果也是先行后列的形式
    return _2[1],_2[0]

def filter_outliers(thermal_point_list,sen_point_list,thresh,method):
    def homo_ca(ref_point_list,sen_point_list):
        # 计算变换矩阵
        MIN_MATCH_COUNT = 4
        if len(ref_point_list) > MIN_MATCH_COUNT:
            src_pts = np.float32([[_[1], _[0]] for _ in ref_point_list]).reshape(-1, 1, 2)
            dst_pts = np.float32([[_[1], _[0]] for _ in sen_point_list]).reshape(-1, 1, 2)
            homo, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            return homo
    def error_cal(ref_point_list,sen_point_list,homo):
        predict_point_list = []
        for points in ref_point_list:
            #输入的points和返回值_2都是先行后列的形式
            _2=get_cores_point(points[0], points[1], homo)
            predict_point_list.append(_2)
            # _ = np.array([points[1], points[0], 1])
            # _2 = np.dot(np.array(homo), (_.T))
            # _2 = _2 / _2[2]
            # predict_point_list.append((_2[1], _2[0]))
        bias_list = []
        for dst, pre in zip(sen_point_list, predict_point_list):
            bias = np.linalg.norm([dst[0] - pre[0], dst[1] - pre[1]])
            bias_list.append(bias)
        bias_mean = np.mean(bias_list)
        max_index=np.argmax(bias_list)
        return bias_mean,max_index

    outliers_ref=[]
    outliers_sen=[]
    if method=='NBCS':
        homo=homo_ca(thermal_point_list,sen_point_list)
        bias_mean,max_index=error_cal(thermal_point_list,sen_point_list,homo)
        while(bias_mean>thresh and len(thermal_point_list)>5):
            _=thermal_point_list.pop(max_index)
            outliers_ref.append(_)
            _=sen_point_list.pop(max_index)
            outliers_sen.append(_)

            homo = homo_ca(thermal_point_list, sen_point_list)
            bias_mean, max_index = error_cal(thermal_point_list, sen_point_list, homo)
    return thermal_point_list,sen_point_list,outliers_ref,outliers_sen


def findHomoraphy(good):
    try:
        MIN_MATCH_COUNT = 4
        if len(good) > MIN_MATCH_COUNT:
            flag=True
            src_pts = np.float32([[_[0], _[1]] for _ in good]).reshape(-1, 1, 2)
            dst_pts = np.float32([[_[2], _[3]] for _ in good]).reshape(-1, 1, 2)
            M, mask = cv2.findHomography(src_pts, dst_pts, 0)
        else:
            print("Not enough matches are found - {}/{}".format(len(good), MIN_MATCH_COUNT))
            M = np.array([[1, 0, 0],
                          [0, 1, 0],
                          [0, 0, 1]], dtype=np.float64)

    except:
        flag = False
        M = np.array([[1, 0, 0],
                      [0, 1, 0],
                      [0, 0, 1]], dtype=np.float64)
    return M,flag


def mosaic_img(img_A,img_B,patch_size=100):
    # 来自H:\mlx_thermal_Images\code\CFOG_testSCBTemplate2\test_fusion
    # 检查并统一图像尺寸
    if img_A.shape[:2] != img_B.shape[:2]:
        # 如果尺寸不匹配，将img_B resize到img_A的尺寸
        original_shape_B = img_B.shape[:2]
        target_height, target_width = img_A.shape[:2]
        img_B = cv2.resize(img_B, (target_width, target_height), interpolation=cv2.INTER_LINEAR)
        print(f"警告: 图像尺寸不匹配，已将img_B从{original_shape_B}调整到{img_A.shape[:2]}")
    
    W, H, C = img_A.shape
    weight = np.zeros([W, H, 3]).astype(np.uint8)
    for m in range(W):
        weight[m] = 1 if int(m / patch_size) % 2 == 0 else 0

    for n in range(H):
        weight[:, n] = 1 - weight[:, n] if int(n / patch_size) % 2 == 1 else weight[:, n]

    alpha = 0.5  # 颜色融合
    alpha = weight  # 镶嵌融合
    fusion = alpha * img_A + (1 - alpha) * img_B
    fusion = fusion.astype(np.uint8)
    return fusion
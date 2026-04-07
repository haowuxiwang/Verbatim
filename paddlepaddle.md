### 安装[¶](https://www.paddleocr.ai/latest/quick_start.html#_1 "Permanent link")

#### 1. 安装PaddlePaddle[¶](https://www.paddleocr.ai/latest/quick_start.html#1-paddlepaddle "Permanent link")

CPU端安装：

```
python-mpipinstallpaddlepaddle==3.2.0-ihttps://www.paddlepaddle.org.cn/packages/stable/cpu/
```

GPU端安装，由于GPU端需要根据具体CUDA版本来对应安装使用，以下仅以Linux平台，pip安装英伟达GPU， CUDA 11.8为例，其他平台，请参考[飞桨官网安装文档](https://www.paddlepaddle.org.cn/install/quick)中的说明进行操作。

```
python-mpipinstallpaddlepaddle-gpu==3.2.0-ihttps://www.paddlepaddle.org.cn/packages/stable/cu118/
```

**请注意，PaddleOCR 3.x版本 依赖于 `3.0` 及以上版本的飞桨框架。**

#### 2. 安装 `paddleocr`[¶](https://www.paddleocr.ai/latest/quick_start.html#2-paddleocr "Permanent link")

执行如下命令安装 PaddleOCR 的完整功能：

```
python-mpipinstall"paddleocr[all]"
```

PaddleOCR 也支持根据需要安装部分功能，详情请参考 [PaddleOCR 安装文档](https://www.paddleocr.ai/latest/version3.x/installation.html)。

### 命令行使用[¶](https://www.paddleocr.ai/latest/quick_start.html#_2 "Permanent link")

[X] [ ] [ ] [ ] [PP-OCRv5](https://www.paddleocr.ai/latest/quick_start.html#__tabbed_1_1)[PP-OCRv5文本检测模块](https://www.paddleocr.ai/latest/quick_start.html#__tabbed_1_2)[PP-OCRv5文本识别模块](https://www.paddleocr.ai/latest/quick_start.html#__tabbed_1_3)[PP-StructureV3](https://www.paddleocr.ai/latest/quick_start.html#__tabbed_1_4)

|

```
paddleocrocr-i./general_ocr_002.png--use_doc_orientation_classifyFalse--use_doc_unwarpingFalse--use_textline_orientationFalse
```

|  |
| - |

### Python脚本使用[¶](https://www.paddleocr.ai/latest/quick_start.html#python "Permanent link")

[X] [ ] [ ] [ ] [PP-OCRv5](https://www.paddleocr.ai/latest/quick_start.html#__tabbed_2_1)[PP-OCRv5文本检测模块](https://www.paddleocr.ai/latest/quick_start.html#__tabbed_2_2)[PP-OCRv5文本识别模块](https://www.paddleocr.ai/latest/quick_start.html#__tabbed_2_3)[PP-StructureV3](https://www.paddleocr.ai/latest/quick_start.html#__tabbed_2_4)

|

```
frompaddleocrimport PaddleOCR

ocr = PaddleOCR(
    use_doc_orientation_classify=False, 
    use_doc_unwarping=False, 
    use_textline_orientation=False) # 文本检测+文本识别
# ocr = PaddleOCR(use_doc_orientation_classify=True, use_doc_unwarping=True) # 文本图像预处理+文本检测+方向分类+文本识别
# ocr = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False) # 文本检测+文本行方向分类+文本识别
# ocr = PaddleOCR(
#     text_detection_model_name="PP-OCRv5_mobile_det",
#     text_recognition_model_name="PP-OCRv5_mobile_rec",
#     use_doc_orientation_classify=False,
#     use_doc_unwarping=False,
#     use_textline_orientation=False) # 更换 PP-OCRv5_mobile 模型
result = ocr.predict("./general_ocr_002.png")
for res in result:
    res.print()
    res.save_to_img("output")
    res.save_to_json("output")
```

|  |
| - |

输出示例：

```
{'res':{'input_path':'/root/.paddlex/predict_input/general_ocr_002.png','page_index':None,'model_settings':{'use_doc_preprocessor':True,'use_textline_orientation':False},'doc_preprocessor_res':{'input_path':None,'page_index':None,'model_settings':{'use_doc_orientation_classify':False,'use_doc_unwarping':False},'angle':-1},'dt_polys':array([[[3,10],
...,
[4,30]],

...,

[[99,456],
...,
[99,479]]],dtype=int16),'text_det_params':{'limit_side_len':736,'limit_type':'min','thresh':0.3,'max_side_limit':4000,'box_thresh':0.6,'unclip_ratio':1.5},'text_type':'general','textline_orientation_angles':array([-1,...,-1]),'text_rec_score_thresh':0.0,'rec_texts':['www.997700','','Cm','登机牌','BOARDING','PASS','CLASS','序号SERIAL NO.','座位号','SEAT NO.','航班FLIGHT','日期DATE','舱位','','W','035','12F','MU2379','03DEc','始发地','FROM','登机口','GATE','登机时间BDT','目的地TO','福州','TAIYUAN','G11','FUZHOU','身份识别IDNO.','姓名NAME','ZHANGQIWEI','票号TKT NO.','张祺伟','票价FARE','ETKT7813699238489/1','登机口于起飞前10分钟关闭 GATESCL0SE10MINUTESBEFOREDEPARTURETIME'],'rec_scores':array([0.67582953,...,0.97418666]),'rec_polys':array([[[3,10],
...,
[4,30]],

...,

[[99,456],
...,
[99,479]]],dtype=int16),'rec_boxes':array([[3,...,30],
...,
[99,...,479]],dtype=int16)}}
```

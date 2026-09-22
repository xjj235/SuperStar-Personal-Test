# -*- coding: utf-8 -*-
import os
import re
import json
from bs4 import BeautifulSoup
from api.logger import logger
from api.font_decoder import FontDecoder

# 是否强制重刷"平台已判定通过"的视频:开启后,isPassed=True 的视频任务不会被跳过,
# 仍会从0播到100%,用于确保每个视频都真正看到100%(代价:重刷较慢,已满100%的也会重播)
RECHECK_PASSED = os.environ.get("CX_RECHECK_PASSED", "").strip().lower() == "true"

# 诊断开关:开启后打印视频卡片的原始 JSON(用于核对平台"已通过"视频的真实进度字段)
DUMP_CARDS = os.environ.get("CX_DUMP_CARDS", "").strip().lower() == "true"


def _num_of(_v, _default: float = 0.0) -> float:
    """安全转数值:卡片里的 headOffset/attDuration 可能是字符串、空值或缺失"""
    try:
        return float(_v)
    except (TypeError, ValueError):
        return _default

def decode_course_list(_text):
    logger.trace("开始解码课程列表...")
    _soup = BeautifulSoup(_text, "lxml")
    _raw_courses = _soup.select("div.course")
    _course_list = list()
    for course in _raw_courses:
        if not course.select_one("a.not-open-tip") and not course.select_one("div.not-open-tip"):
            _course_detail = {}
            _course_detail["id"] = course.attrs["id"]
            _course_detail["info"] = course.attrs["info"]
            _course_detail["roleid"] = course.attrs["roleid"]

            _course_detail["clazzId"] = course.select_one("input.clazzId").attrs["value"]
            _course_detail["courseId"] = course.select_one("input.courseId").attrs["value"]
            _course_detail["cpi"] = re.findall(r"cpi=(.*?)&", course.select_one("a").attrs["href"])[0]
            _course_detail["title"] = course.select_one("span.course-name").attrs["title"]
            if course.select_one("p.margint10") is None:
                _course_detail["desc"] = ''
            else:
                _course_detail["desc"] = course.select_one("p.margint10").attrs["title"]
            _course_detail["teacher"] = course.select_one("p.color3").attrs["title"]
            _course_list.append(_course_detail)
    return _course_list

def decode_course_folder(_text):
    logger.trace("开始解码二级课程列表...")
    _soup = BeautifulSoup(_text, "lxml")
    _raw_courses = _soup.select("ul.file-list>li")
    _course_folder_list = list()
    for course in _raw_courses:
        if course.attrs["fileid"]:
            _course_folder_detail = {}
            _course_folder_detail["id"] = course.attrs["fileid"]
            _course_folder_detail["rename"] = course.select_one("input.rename-input").attrs["value"]
            _course_folder_list.append(_course_folder_detail)
    return _course_folder_list

def decode_course_point(_text):
    logger.trace("开始解码章节列表...")
    _soup = BeautifulSoup(_text, "lxml")
    _course_point = {
        "hasLocked": False,     # 用于判断该课程任务是否是需要解锁
        "points": []
    }
    
    
    for _chapter_unit in _soup.find_all("div",class_="chapter_unit") :
        _point_list = []
        _raw_points = _chapter_unit.find_all("li")
        for _point in _raw_points:
            _point = _point.div
            if (not "id" in _point.attrs):
                continue
            _point_detail = {}
            _point_detail["id"] = re.findall(r"^cur(\d{1,20})$", _point.attrs["id"])[0]
            _clicktitle = _point.select_one("a.clicktitle")
            _point_detail["title"] = _clicktitle.text.replace("\n", '').strip(' ') if _clicktitle is not None else ''
            _point_detail["jobCount"] = 1   # 默认为1
            if _point.select_one("input.knowledgeJobCount"):
                _point_detail["jobCount"] = _point.select_one("input.knowledgeJobCount").attrs["value"]
            else:
                # 判断是不是因为需要解锁
                _tip = _point.select_one("span.bntHoverTips")
                if _tip is not None and '解锁' in _tip.text:
                    _course_point["hasLocked"] = True
            
            _point_list.append(_point_detail)
        _course_point["points"]+=_point_list
    return _course_point


def decode_course_card(_text: str):
    logger.trace("开始解码任务点列表...")
    _job_info = {}
    _job_list = []
    # 对于未开放章节检测
    if '章节未开放' in _text:
        _job_info['notOpen'] = True
        return [],_job_info
    
    _temp = re.findall(r"mArg=\{(.*?)\};", _text.replace(" ", ""))
    if _temp:
        _temp = _temp[0]
    else:
        return [],{}
    _cards = json.loads("{" + _temp + "}")
   
    if _cards:
        _job_info = {}
        _job_info["ktoken"] = _cards["defaults"]["ktoken"]
        _job_info["mtEnc"] = _cards["defaults"]["mtEnc"]
        _job_info["reportTimeInterval"] = _cards["defaults"]["reportTimeInterval"]   # 60
        _job_info["defenc"] = _cards["defaults"]["defenc"]
        _job_info["cardid"] = _cards["defaults"]["cardid"]
        _job_info["cpi"] = _cards["defaults"]["cpi"]
        _job_info["qnenc"] = _cards["defaults"]["qnenc"]
        _job_info['knowledgeid'] = _cards["defaults"]["knowledgeid"]
        _cards = _cards["attachments"]
        _job_list = []
        _passed_video = 0      # 平台标记"已通过"且确实已满100%而被跳过的视频数
        _passed_other = 0      # 其它被跳过的已完成任务数
        _redone_video = 0      # 平台标记"已通过"但实际未满100%,需要重刷的视频数
        for _card in _cards:
            # 卡片缺少 type 字段(转码中/异常卡片):直接跳过,避免 KeyError 导致整个章节读取失败
            if not _card.get("type"):
                continue
            # 已经通过的任务
            if "isPassed" in _card and _card["isPassed"] is True:
                _ctype = _card.get("type")
                _vname = (_card.get('property') or {}).get('name', '')
                # 关键修复:平台在约90%进度时就会把视频标记为 isPassed=True,
                # 若只用 isPassed 判断,就会把"没看到100%"的视频永久跳过。
                # 这里按卡片里的真实进度判定:headOffset(毫秒) 达到 attDuration(秒)*1000 才算真看完。
                _finished = True
                _head = _dur = 0.0
                if _ctype == "video":
                    _head = _num_of(_card.get("headOffset")) / 1000.0   # 已看进度(秒)
                    _dur = _num_of(_card.get("attDuration"))            # 总时长(秒)
                    if _dur > 0:
                        _finished = _head >= _dur - 1                   # 允许1秒误差
                # 需要重刷:视频没真正看完;或开启 CX_RECHECK_PASSED 强制全量重刷
                if _ctype == "video" and ((not _finished) or RECHECK_PASSED):
                    if _finished:
                        logger.info(f"强制重刷已完成视频(CX_RECHECK_PASSED=true): {_vname}")
                    else:
                        _redone_video += 1
                        logger.warning(f"视频平台标记通过但未满100%({_head:.0f}/{_dur:.0f}秒),将重刷到100%: {_vname}")
                    # 不 continue:继续往下解析,生成待处理任务交给 study_video 重刷
                else:
                    if _ctype == "video":
                        _passed_video += 1
                        if DUMP_CARDS:
                            logger.info(f"[卡片] video 已满100%({_head:.0f}/{_dur:.0f}秒),跳过: {_vname}")
                    else:
                        _passed_other += 1
                    continue
            # 不属于任务点的任务
            if "job" not in _card or _card["job"] is False:
                if _card.get('type') and _card['type'] == "read":
                    # 发现有在视频任务下掺杂阅读任务，不完成可能会导致无法开启下一章节
                    if _card['property'].get('read',False):
                        # 已阅读，跳过
                        continue
                    _job = {}
                    _job['title'] = _card['property']['title']
                    _job["type"] = "read"
                    _job['id'] = _card['property']['id']
                    _job["jobid"] = _card["jobid"]
                    _job["jtoken"] = _card["jtoken"]
                    _job['mid'] = _card['mid']
                    _job['otherinfo'] = _card["otherInfo"]
                    _job['enc'] = _card["enc"]
                    _job['aid'] = _card["aid"]
                    _job_list.append(_job)
                continue
            # 视频任务
            if _card["type"] == "video":
                _job = {}
                _job["type"] = "video"
                if not _card.get("objectId") or not _card.get("property"):
                    logger.warning("视频卡片信息不完整(缺 objectId/property)，已跳过...")
                    continue
                _job["jobid"] = _card["jobid"]
                _job["name"] = (_card.get("property") or {}).get("name", "")
                _job["otherinfo"] = _card["otherInfo"]
                try:
                    _job["mid"] = _card["mid"]
                except KeyError:
                    logger.warning("出现转码失败视频，已跳过...")
                    continue
                _job["objectid"] = _card["objectId"]
                _job["aid"] = _card["aid"]
                # _job["doublespeed"] = _card["property"]["doublespeed"]
                _job_list.append(_job)
                continue
            if _card["type"] == "document":
                _job = {}
                _job["type"] = "document"
                _job["jobid"] = _card["jobid"]
                _job["otherinfo"] = _card["otherInfo"]
                _job["jtoken"] = _card["jtoken"]
                _job["mid"] = _card["mid"]
                _job["enc"] = _card["enc"]
                _job["aid"] = _card["aid"]
                _job["objectid"] = (_card.get("property") or {}).get("objectid", "")
                if not _job["objectid"]:
                    logger.warning("文档卡片缺少 objectid，已跳过...")
                    continue
                _job_list.append(_job)
                continue
            if _card["type"] == "workid":
                # 章节检测
                _job = {}
                _job["type"] = "workid"
                _job["jobid"] = _card["jobid"]
                _job["otherinfo"] = _card["otherInfo"]
                _job["mid"] = _card["mid"]
                _job["enc"] = _card["enc"]
                _job["aid"] = _card["aid"]
                _job_list.append(_job)
                continue
       
            if _card["type"] == "vote":
                # 调查问卷 同上
                continue
        # 供上层统计:本章节有多少任务被平台标记"已通过"而跳过 / 有多少视频需重刷到100%
        _job_info['skipped_passed_video'] = _passed_video
        _job_info['skipped_passed_other'] = _passed_other
        _job_info['redone_video'] = _redone_video
        return _job_list, _job_info
    

def extract_images_from_div(div_html: str) -> list:
    """
    从题目 div 的 HTML 片段中提取所有图片 URL(支持 src 与懒加载 data-src),
    并补全相对协议(//)与相对路径(/)。
    返回完整 URL 列表,如无图片则返回空列表。
    """
    images = []
    for m in re.finditer(r'<img[^>]*>', div_html, re.I):
        tag = m.group(0)
        url = None
        src_m = re.search(r'src=["\']?([^"\' >]+)', tag, re.I)
        data_src_m = re.search(r'data-src=["\']?([^"\' >]+)', tag, re.I)
        if src_m:
            url = src_m.group(1)
        elif data_src_m:
            url = data_src_m.group(1)
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://mooc1.chaoxing.com" + url
        if url.startswith("http") and url not in images:
            images.append(url)
    return images


def decode_questions_info(html_content) -> dict:
    def replace_rtn(text):
        return text.replace('\r', '').replace('\t', '').replace('\n', '')

    soup = BeautifulSoup(html_content, "lxml")
    form_data = {}
    form_tag = soup.find("form")
    if form_tag is None:
        # 未找到表单,直接返回(避免后续崩溃)
        logger.warning("未找到表单元素,返回空题目信息")
        return form_data

    # 提取表单真实提交地址(学习通 action 带 token/enc 等查询参数,必须用它提交)
    action = (form_tag.get("action") or "")
    if action.startswith("/"):
        submit_url = "https://mooc1.chaoxing.com" + action
    elif action.startswith("//"):
        submit_url = "https:" + action
    elif action.startswith("http"):
        submit_url = action
    else:
        submit_url = "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew"
    form_data["submit_url"] = submit_url

    fd = FontDecoder(html_content)  # 加载字体
    
    # 抽取表单信息
    for input_tag in form_tag.find_all("input"):
        if 'name' not in input_tag.attrs or 'answer' in input_tag.attrs["name"]:
            continue
        form_data.update({
            input_tag.attrs["name"]: input_tag.attrs.get("value",'')
        })

    form_data['questions'] = []
    for div_tag in form_tag.find_all("div",class_="singleQuesId"): # 目前来说无论是单选还是多选的题class都是这个
        _zt = div_tag.find("div", class_="Zy_TItle")
        q_title = replace_rtn(fd.decode(_zt.text)) if _zt is not None else ''
        q_options = ''
        opt_data = []            # 题目每个选项的真实 data 值(按 A,B,C... 顺序),用于按题目实际要求填答
        _ul = div_tag.find("ul")
        for li_tag in (_ul.find_all("li") if _ul is not None else []):
            q_options += replace_rtn(fd.decode(li_tag.text))+'\n'
            _sp = li_tag.find("span")
            if _sp is not None and _sp.get("data"):
                opt_data.append(str(_sp.get("data")))
        q_options=q_options[:-1]    # 去除尾部'\n'

        # 尝试使用 data 属性来判断题型
        _tm = div_tag.find('div',class_='TiMu')
        q_type_code = _tm.attrs['data'] if _tm is not None else '0'
        q_type = ''
        # 此处可能需要完善更多题型的判断
        if q_type_code == '0':
            q_type = 'single'
        elif q_type_code == '1':
            q_type = 'multiple'
        elif q_type_code == '2':
            q_type = 'completion'
        elif q_type_code == '3':
            q_type = 'judgement'
        else:
            logger.info("未知题型代码 -> "+q_type_code)
            q_type = 'unknown'      # 避免出现未定义取值错误

        # 提取该题(题目+选项区域)内的图片,供 AI 视觉答题使用
        q_images = extract_images_from_div(str(div_tag))

        form_data["questions"].append({
            'id': div_tag.attrs["data"],
            'title':q_title,      # 题目
            'options':q_options,    # 选项 可提供给题库作为辅助
            'type': q_type,      # 题型 可提供给题库作为辅助
            'images': q_images,  # 题目/选项中的图片URL列表(可能为空)
            'option_data': opt_data,  # 每题选项的真实 data 值(按 A,B,C 顺序),用于按题目要求填答
            'answerField':{
                'answer'+div_tag.attrs["data"]:'',   # 答案填入处
                'answertype'+div_tag.attrs["data"]:q_type_code
            }
            })
    # 处理答题信息
    form_data['answerwqbid'] = ",".join([q['id'] for q in form_data['questions']])+","
    return form_data


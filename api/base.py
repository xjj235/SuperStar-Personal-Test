# -*- coding: utf-8 -*-
import os
import re
import time
import random
import requests
from hashlib import md5
from requests.adapters import HTTPAdapter

from api.cipher import AESCipher
from api.logger import logger
from api.cookies import save_cookies, use_cookies
from api.process import show_progress
from api.config import GlobalConst as gc
from api.decode import (decode_course_list,
                        decode_course_point,
                        decode_course_card,
                        decode_course_folder,
                        decode_questions_info
                        )
from api.answer import *
from api.deadline import check as check_deadline, can_start as can_start_task

# 填空题多空答案的分隔符(学习通按空存库;不同题目可能要求不同,可用环境变量 CX_BLANK_SEP 覆盖)
BLANK_SEP = os.environ.get("CX_BLANK_SEP", "，")

def get_timestamp():
    return str(int(time.time() * 1000))


def get_random_seconds():
    return random.randint(30, 90)


def init_session(isVideo: bool = False, isAudio: bool = False):
    _session = requests.session()
    _session.verify = False
    _session.mount('http://', HTTPAdapter(max_retries=3))
    _session.mount('https://', HTTPAdapter(max_retries=3))
    if isVideo:
        _session.headers = gc.VIDEO_HEADERS
    elif isAudio:
        _session.headers = gc.AUDIO_HEADERS
    else:
        _session.headers = gc.HEADERS
    _session.cookies.update(use_cookies())
    return _session


class Account:
    username = None
    password = None
    last_login = None
    isSuccess = None
    def __init__(self, _username, _password):
        self.username = _username
        self.password = _password


class Chaoxing:
    def __init__(self, account: Account = None,tiku:Tiku=None):
        self.account = account
        self.cipher = AESCipher()
        self.tiku = tiku

    def login(self):
        _session = requests.session()
        _session.verify = False
        _url = "https://passport2.chaoxing.com/fanyalogin"
        _data = {"fid": "-1",
                    "uname": self.cipher.encrypt(self.account.username),
                    "password": self.cipher.encrypt(self.account.password),
                    "refer": "https%3A%2F%2Fi.chaoxing.com",
                    "t": True,
                    "forbidotherlogin": 0,
                    "validate": "",
                    "doubleFactorLogin": 0,
                    "independentId": 0,
                }
        
        logger.trace("正在尝试登录...")
        resp = _session.post(_url, headers=gc.HEADERS, data=_data)
        if resp and resp.json()["status"] == True:
            save_cookies(_session)
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}
        else:
            return {"status": False, "msg": str(resp.json()["msg2"])}

    def get_fid(self):
        _session = init_session()
        return _session.cookies.get("fid")

    def get_uid(self):
        _session = init_session()
        return _session.cookies.get("_uid")

    def get_course_list(self):
        _session = init_session()
        _url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        _data = {
            "courseType": 1,
            "courseFolderId": 0,
            "query": "",
            "superstarClass": 0
        }
        logger.trace("正在读取所有的课程列表...")
        # 接口突然抽风，增加headers
        _headers = {
            "Host": "mooc2-ans.chaoxing.com",
            "sec-ch-ua-platform": "\"Windows\"",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
            "Accept": "text/html, */*; q=0.01",
            "sec-ch-ua": "\"Microsoft Edge\";v=\"129\", \"Not=A?Brand\";v=\"8\", \"Chromium\";v=\"129\"",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "sec-ch-ua-mobile": "?0",
            "Origin": "https://mooc2-ans.chaoxing.com",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5"
        }
        _resp = _session.post(_url,headers=_headers,data=_data)
        # logger.trace(f"原始课程列表内容:\n{_resp.text}")
        logger.info("课程列表读取完毕...")
        course_list = decode_course_list(_resp.text)

        _interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
        _interaction_resp = _session.get(_interaction_url)
        course_folder = decode_course_folder(_interaction_resp.text)
        for folder in course_folder:
            _data = {
                "courseType": 1,
                "courseFolderId": folder["id"],
                "query": "",
                "superstarClass": 0
            }
            _resp = _session.post(_url, data=_data)
            course_list += decode_course_list(_resp.text)
        return course_list

    def get_course_point(self, _courseid, _clazzid, _cpi):
        _session = init_session()
        _url = f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid={_courseid}&clazzid={_clazzid}&cpi={_cpi}&ut=s"
        logger.trace("开始读取课程所有章节...")
        _resp = _session.get(_url)
        # logger.trace(f"原始章节列表内容:\n{_resp.text}")
        logger.info("课程章节读取成功...")
        return decode_course_point(_resp.text)

    def get_job_list(self, _clazzid, _courseid, _cpi, _knowledgeid):
        _session = init_session()
        job_list = []
        job_info = {}
        for _possible_num in ["0", "1","2"]:    # 学习界面任务卡片数，很少有3个的，但是对于章节解锁任务点少一个都不行，可以从API /mooc-ans/mycourse/studentstudyAjax获取值，或者干脆直接加，但二者都会造成额外的请求
            _url = f"https://mooc1.chaoxing.com/mooc-ans/knowledge/cards?clazzid={_clazzid}&courseid={_courseid}&knowledgeid={_knowledgeid}&num={_possible_num}&ut=s&cpi={_cpi}&v=20160407-3&mooc2=1"
            logger.trace("开始读取章节所有任务点...")
            _resp = _session.get(_url)
            _job_list, _job_info = decode_course_card(_resp.text)
            if _job_info.get('notOpen',False):
                # 直接返回，节省一次请求
                logger.info("该章节未开放")
                return [], _job_info
            job_list += _job_list
            job_info.update(_job_info)
            # if _job_list and len(_job_list) != 0:
            #     break
        # logger.trace(f"原始任务点列表内容:\n{_resp.text}")
        logger.info("章节任务点读取成功...")
        return job_list, job_info

    def get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]"
            .encode()).hexdigest()

    def video_progress_log(self, _session, _course, _job, _job_info, _dtoken, _duration, _playingTime, _type: str = "Video"):
        if "courseId" in _job['otherinfo']:
            _mid_text = f"otherInfo={_job['otherinfo']}&"
        else:
            _mid_text = f"otherInfo={_job['otherinfo']}&courseId={_course['courseId']}&"
        _success = False
        for _possible_rt in ["0.9", "1"]:
            _url = (f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
                    f"{_course['cpi']}/"
                    f"{_dtoken}?"
                    f"clazzId={_course['clazzId']}&"
                    f"playingTime={_playingTime}&"
                    f"duration={_duration}&"
                    f"clipTime=0_{_duration}&"
                    f"objectId={_job['objectid']}&"
                    f"{_mid_text}"
                    f"jobid={_job['jobid']}&"
                    f"userid={self.get_uid()}&"
                    f"isdrag=3&"
                    f"view=pc&"
                    f"enc={self.get_enc(_course['clazzId'], _job['jobid'], _job['objectid'], _playingTime, _duration, self.get_uid())}&"
                    f"rt={_possible_rt}&"
                    f"dtype={_type}&"
                    f"_t={get_timestamp()}")
            resp = _session.get(_url)
            if resp.status_code == 200:
                _success = True
                break # 如果返回为200正常，则跳出循环
            elif resp.status_code == 403:
                continue # 如果出现403无权限报错，则继续尝试不同的rt参数
        if _success:
            return resp.json()
        else:
            # 若出现两个rt参数都返回403的情况，则跳过当前任务
            logger.warning("出现403报错，尝试修复无效，正在跳过当前任务点...")
            return False

    def study_video(self, _course, _job, _job_info, _speed: float = 1.0, _type: str = "Video"):
        if _type == "Video":
            _session = init_session(isVideo=True)
        else:
            _session = init_session(isAudio=True)
        _session.headers.update()
        _info_url = f"https://mooc1.chaoxing.com/ananas/status/{_job['objectid']}?k={self.get_fid()}&flag=normal"
        _video_info = _session.get(_info_url).json()
        # 修复:视频信息获取失败时原先静默跳过(无任何日志),导致"有些视频没刷"却查不出原因
        if not isinstance(_video_info, dict) or _video_info.get("status") != "success":
            logger.warning(f"视频信息获取失败(status={_video_info.get('status') if isinstance(_video_info, dict) else type(_video_info).__name__}),跳过该任务点: {_job.get('name', '')}")
            return
        if _video_info["status"] == "success":
            _dtoken = _video_info["dtoken"]
            _duration = _video_info["duration"]
            _crc = _video_info["crc"]
            _key = _video_info["key"]
            _isPassed = False
            _isFinished = False
            _playingTime = 0
            logger.info(f"开始任务: {_job['name']}, 总时长: {_duration}秒")
            # 软预算:剩余时间不足以播完这个视频时,不开始播放,留到下一轮继续
            # (避免"开始了一个超长视频,结果撞上 GitHub 6 小时硬上限被强杀,导致接力链中断")
            if not can_start_task(int(_duration)):
                logger.warning(f"本轮剩余时间不足以播完该视频({_duration}秒),留待下一轮继续: {_job['name']}")
                return
            while not _isFinished:
                # 硬预算:播放过程中(每上报一次进度)检查一次,达到上限立即优雅收尾
                check_deadline()
                _isPassed = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, _duration, _playingTime, _type)
                # 修改:不再因系统判定90%已通过(isPassed=True)而提前结束，
                # 必须把视频/音频看到100%(_playingTime达到总时长)才算完成
                if not _isPassed:
                    # 进度上报失败(如403修复无效)，跳过当前任务
                    logger.warning("进度上报失败，跳过当前任务点...")
                    break
                if _playingTime >= int(_duration):
                    # 播放进度已达到100%，结束任务
                    _isFinished = True
                    break
                _wait_time = get_random_seconds()
                if _playingTime + _wait_time >= int(_duration):
                    _wait_time = int(_duration) - _playingTime
                # 播放进度条
                show_progress(_job['name'], _playingTime, _wait_time, _duration, _speed)
                _playingTime += _wait_time
            print("\r", end="", flush=True)
            logger.info(f"任务完成: {_job['name']}")

    def study_document(self, _course, _job):
        _session = init_session()
        _url = f"https://mooc1.chaoxing.com/ananas/job/document?jobid={_job['jobid']}&knowledgeid={re.findall(r'nodeId_(.*?)-', _job['otherinfo'])[0]}&courseid={_course['courseId']}&clazzid={_course['clazzId']}&jtoken={_job['jtoken']}&_dc={get_timestamp()}"
        _resp = _session.get(_url)

    def _clean_text_answer(self, text, q=None) -> str:
        """填空题/简答题文本答案清洗与"转义"处理:
        - 去掉换行/制表符/控制字符(换行是导致填空答案提交异常的主因),压缩多余空格与全角空格;
        - 多空题:按题面中空的数量,把答案按空拆开再用统一分隔符连接(分隔符可用环境变量 CX_BLANK_SEP 覆盖);
        - 去掉成对的引号/书名号等包裹符号,避免平台存库时转义异常;
        - 若答案只剩符号(如 ***),返回空串表示不可用(交由 study_work 兜底)。"""
        t = str(text or '')
        t = re.sub(r'[\r\n\t\x00-\x08\x0b\x0c\x0e-\x1f]+', ' ', t)   # 换行/控制字符 -> 空格
        t = re.sub(r'[\u3000\s]+', ' ', t).strip()                    # 全角/半角空白压缩
        if not t:
            return ''
        # 去掉整体包裹的引号/括号(仅一层)
        t = re.sub(r'^[“"‘\'〈《【\[\(]+', '', t)
        t = re.sub(r'[”"’\'〉》】\]\)]+$', '', t).strip()
        if not re.sub(r'[\W_]+', '', t, flags=re.UNICODE):
            return ''   # 只剩符号(如 ***)视为无效答案
        # 多空题:按空拆分并用统一分隔符连接
        title = str((q or {}).get('title') or '')
        blanks = len(re.findall(r'_{2,}', title))
        if blanks > 1:
            parts = [p for p in re.split(r'[ ,，、;；|/]+', t) if p]
            if len(parts) == blanks:      # 拆分数量与空数一致才改写,否则保持原样
                return BLANK_SEP.join(parts)
        return t

    def _match_answer(self, res, q, forced=False) -> str:
        """把 DeepSeek 答案匹配成【学习通该题的实际填写值】。
        优先使用题目选项的真实 data 值(option_data);无则回退到标准字母/true-false。
        填空题/简答题:直接使用文本答案本身,绝不取首字母或字母。"""
        qtype = q.get('type')
        opt_data = q.get('option_data') or []
        r = str(res).strip()
        # 填空题/简答题:答案就是文本本身(修复:原先会取首字符,再被强制降级成 A)
        if qtype in ('completion', 'unknown'):
            return self._clean_text_answer(r, q)
        if qtype == 'multiple':
            letters = sorted(set(ch for ch in r.upper() if ch in "ABCDEFGH"))
            vals = self._data_of(letters, opt_data)
            return (vals or ('A' if forced else ''))
        if qtype == 'judgement':
            is_true = r in ('正确', '对', '√', '是', 'true', 'TRUE', 'T', 'A')
            # 按题目实际:找表示"对/正确"或"错/错误"的选项 data 值
            for d in opt_data:
                dl = str(d).lower()
                if is_true and dl in ('true', '1', '对', '正确', '是', '√', '肯定'):
                    return str(d)
                if not is_true and dl in ('false', '0', '错', '错误', '否', '×', '否定'):
                    return str(d)
            return 'true' if is_true else 'false'
        # single / unknown
        letters = [ch for ch in r.upper() if ch in "ABCDEFGH"]
        if letters:
            return self._data_of(letters, opt_data) or letters[0]
        if forced:
            return 'A'
        return (r[:1] if r else '')

    def _data_of(self, letters, opt_data) -> str:
        """按题目选项真实 data 值取答案:每个选项字母 → 对应 data 值(无则用字母本身)"""
        vals = []
        for ch in letters:
            idx = ord(ch) - ord('A')
            if 0 <= idx < len(opt_data) and opt_data[idx]:
                vals.append(str(opt_data[idx]))
            elif ch.isalpha():
                vals.append(ch)
        return "".join(vals)

    def _verify_consistent(self, res, answer, q) -> bool:
        """校验 DeepSeek 答案 与 系统填写值 是否一致(按题目实际要求,无转义失败/答案不一致)"""
        qtype = q.get('type')
        opt_data = q.get('option_data') or []
        a = str(answer or '')
        if qtype == 'judgement':
            # 判断题填 true/false,或等于该题选项的 data 值,即合法
            return a in ('true', 'false') or (bool(opt_data) and a in [str(x) for x in opt_data])
        if qtype in ('completion', 'unknown'):
            # 填空题/简答题:填写值就是文本答案本身,只要清洗后仍有有效内容即合法
            # (原先会做字母校验 -> 必然失败 -> 被强制降级成 A,这里修复)
            return bool(re.sub(r'[\W_]+', '', a, flags=re.UNICODE))
        res_letters = sorted(set(ch for ch in str(res).upper() if ch in "ABCDEFGH"))
        if not res_letters:
            return False
        if qtype == 'multiple':
            # 多选题:按题目 option_data 正向映射后应与填写值完全一致。
            # 注意:部分题目的选项 data 值是乱序字母(如 res=B,D 时正确填写值是 "DA"),
            # 原先用"填写的字母集合 ⊆ res 字母集合"判断会误报"答案不一致",这里改为正向比对。
            expected = self._data_of(res_letters, opt_data)
            return a == expected or a.upper() == expected.upper()
        # single / 其它:填写值应等于 res 对应选项的 data 值(无 data 时即字母本身)
        expected = self._data_of(res_letters[:1], opt_data)
        return a == expected or a.upper() == expected.upper()

    def study_work(self, _course, _job,_job_info) -> None:
        if self.tiku.DISABLE or not self.tiku:
            return None
        _ORIGIN_HTML_CONTENT = ""   # 用于配合输出网页源码，帮助修复#391错误

        def random_answer(options:str) -> str:
            answer = ''
            if not options:
                return answer
            
            if q['type'] == "multiple":
                _op_list = multi_cut(options)
                for i in range(random.choices([2,3,4],weights=[0.1,0.5,0.4],k=1)[0]):   # 此处表示随机多选答案几率：2个 10%，3个 50% ，4个 40%
                    _choice = random.choice(_op_list)
                    _op_list.remove(_choice)
                    answer+=_choice[:1] # 取首字为答案，例如A或B
                # 对答案进行排序，否则会提交失败
                answer = "".join(sorted(answer))
            elif q['type'] == "single":
                answer = random.choice(options.split('\n'))[:1] # 取首字为答案，例如A或B
            # 判断题处理
            elif q['type'] == "judgement":
                # answer = self.tiku.jugement_select(_answer)
                answer = "true" if random.choice([True,False]) else "false"
            logger.info(f'随机选择 -> {answer}')
            return answer
        
        def multi_cut(answer:str) -> list[str]:
            cut_char = [',','，','|','\n','\r','\t','#','*','-','_','+','@','~','/','\\','.','&',' ']    # 多选答案切割符
            res = []
            for char in cut_char:
                res = answer.split(char)
                if len(res)>1:
                    return res
            # 多选答案是单个选项字母(如 D)时,直接返回该字母,避免默认ABCD导致全选
            s = (answer or '').strip().upper()
            if len(s) == 1 and s in "ABCDEFGH":
                return [s]
            logger.warning("未能正确提取题目选项信息，请反馈。")
            return ['A','B','C','D']    # 默认多选题为4个选项


        # 学习通这里根据参数差异能重定向至两个不同接口，需要定向至https://mooc1.chaoxing.com/mooc-ans/workHandle/handle
        _session = init_session()
        headers={
            "Host": "mooc1.chaoxing.com",
            "sec-ch-ua": "\"Microsoft Edge\";v=\"129\", \"Not=A?Brand\";v=\"8\", \"Chromium\";v=\"129\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5"
        }
        cookies = _session.cookies.get_dict()


        _url = "https://mooc1.chaoxing.com/mooc-ans/api/work"   
        _resp = requests.get(
            _url,
            headers=headers,
            cookies=cookies,
            verify=False,
            params = {
                "api": "1",
                "workId": _job['jobid'].replace("work-",""),
                "jobid": _job['jobid'],
                "originJobId": _job['jobid'],
                "needRedirect": "true",
                "skipHeader": "true",
                "knowledgeid": str(_job_info['knowledgeid']),
                'ktoken': _job_info['ktoken'], 
                "cpi": _job_info['cpi'],
                "ut": "s",
                "clazzId": _course['clazzId'],
                "type": "",
                "enc": _job['enc'],
                "mooc2": "1",
                "courseid": _course['courseId']
            }
        )
        _ORIGIN_HTML_CONTENT = _resp.text   # 用于配合输出网页源码，帮助修复#391错误
        questions = decode_questions_info(_resp.text)   # 加载题目信息

        # 搜题
        for q in questions['questions']:
            # 硬预算:每答一题检查一次,避免单个大作业答题耗时过长撞上 6 小时上限被强杀
            # (抛出的 TimeBudgetExceeded 继承 BaseException,不会被上层 except Exception 吞掉,
            #  作业也不会以"半截答案"提交,下一轮会重新作答)
            check_deadline()
            res = self.tiku.query(q)
            answer = ''
            if not res:
                # 四级(推理/重问/网页搜索/最接近)均失败:给客观"最接近"答案,保证提交成功、不随机、不留空
                # 经 _match_answer 按题目真实答题要求映射(如判断题填该题实际的 对/错 或 true/false,不硬编码)
                closest = '正确' if q['type'] == 'judgement' else 'A'
                answer = self._match_answer(closest, q)
                logger.error(f"题目四级均无答案,使用最接近答案({answer}): {q['title']}")
            else:
                # 匹配:把 DeepSeek 答案按题目实际要求填写
                answer = self._match_answer(res, q)
                # 校验:DeepSeek 答案与系统填写是否一致;不一致则重新匹配
                if not self._verify_consistent(res, answer, q):
                    logger.error(f"答案不一致或转义异常(res={res}, fill={answer}),重新匹配: {q['title']}")
                    answer = self._match_answer(res, q, forced=True)
                # 防线:分题型处理,避免把填空题的文本答案误改成字母
                if q['type'] in ('completion', 'unknown'):
                    # 填空题/简答题:保留文本答案(文本可能含 * 等符号,不做星号清洗);仅当为空时兜底
                    if not str(answer).strip():
                        answer = 'A'
                elif '*' in str(answer) or not answer:
                    answer = 'false' if q['type'] == 'judgement' else 'A'
            # 填充答案
            q['answerField'][f'answer{q["id"]}'] = answer
            if q['type'] in ('completion', 'unknown'):
                logger.info(f'{q["title"]} 填写答案为(填空/简答) {answer}')
            else:
                logger.info(f'{q["title"]} 填写答案为 {answer}')
        
        # 提交模式  现在与题库绑定
        questions['pyFlag'] = self.tiku.get_submit_params()  

        # 组建提交表单
        for q in questions["questions"]:
            questions.update({
                f'answer{q["id"]}':q['answerField'][f'answer{q["id"]}'],
                f'answertype{q["id"]}':q['answerField'][f'answertype{q["id"]}']
            })


        del questions["questions"]

        # 使用学习通实际表单提交地址(含 token/enc 等查询参数,否则提交会被拒绝)
        submit_url = questions.get("submit_url") or 'https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew'
        res = _session.post(
            submit_url,
            data=questions,
            headers= {
                "Host": "mooc1.chaoxing.com",
                "sec-ch-ua-platform": "\"Windows\"",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "sec-ch-ua": "\"Microsoft Edge\";v=\"129\", \"Not=A?Brand\";v=\"8\", \"Chromium\";v=\"129\"",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "sec-ch-ua-mobile": "?0",
                "Origin": "https://mooc1.chaoxing.com",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                #"Referer": "https://mooc1.chaoxing.com/mooc-ans/work/doHomeWorkNew?courseId=246831735&workAnswerId=52680423&workId=37778125&api=1&knowledgeid=913820156&classId=107515845&oldWorkId=07647c38d8de4c648a9277c5bed7075a&jobid=work-07647c38d8de4c648a9277c5bed7075a&type=&isphone=false&submit=false&enc=1d826aab06d44a1198fc983ed3d243b1&cpi=338350298&mooc2=1&skipHeader=true&originJobId=work-07647c38d8de4c648a9277c5bed7075a",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5"
            }
        )
        if res.status_code == 200:
            res_json = res.json()
            if res_json['status']:
                logger.info(f'提交答题成功 -> {res_json["msg"]}')
            else:
                logger.error(f'提交答题失败 -> {res_json["msg"]}')
        else:
            logger.error(f"提交答题失败 -> {res.text}")

    def strdy_read(self, _course, _job,_job_info) -> None:
        """
        阅读任务学习，仅完成任务点，并不增长时长
        """
        _session = init_session()
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/ananas/job/readv2",
            params={
                'jobid': _job['jobid'],
                'knowledgeid':_job_info['knowledgeid'],
                'jtoken': _job['jtoken'],
                'courseid': _course['courseId'],
                'clazzid': _course['clazzId']
            }
        )
        if _resp.status_code != 200:
            logger.error(f"阅读任务学习失败 -> [{_resp.status_code}]{_resp.text}")
        else:
            _resp_json = _resp.json()
            logger.info(f"阅读任务学习 -> {_resp_json['msg']}")

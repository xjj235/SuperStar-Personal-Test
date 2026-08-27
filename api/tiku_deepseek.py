# -*- coding: utf-8 -*-
"""
DeepSeek AI 答题 provider
- TikuDeepSeek:调用 DeepSeek API 答题
  流程(全部使用 flash 系列模型,禁止使用 PRO 模型):
    纯文字题 → 直接调用文本 flash 模型 deepseek-v4-flash 推理答案
    图片题   → ① 先用视觉模型 deepseek-v4-flash-vision-exp 把图片识别为文字(OCR)
              ② 把【图片识别出的文字 + 题目 + 选项】拼成纯文本,统一交给
                 deepseek-v4-flash 文本 flash 模型推理答案

密钥读取顺序:环境变量 DEEPSEEK_API_KEY(云端 GitHub Actions 注入) > 配置文件 [tiku] api_key(本地)
"""
import os
import time
import base64
import requests
from api.answer import Tiku
from api.logger import logger

DEEPSEEK_BASE = "https://api.deepseek.com"
WEB_SEARCH_BASE = "https://api.deepseek.com/anthropic/v1"   # 联网搜索用 Anthropic 兼容端点(不同于 chat-completions)
TEXT_MODEL = "deepseek-v4-flash"                 # 文本 flash 模型,统一用于答题推理(禁止使用 PRO)
WEB_SEARCH_MODEL = "deepseek-v4-flash"           # 联网搜索模型
VISION_MODEL = "deepseek-v4-flash-vision-exp"    # 视觉模型,仅用于把图片识别为文字
TIMEOUT = 90
MAX_TOKENS = 4096   # deepseek-v4-flash(-vision-exp) 是推理模型,token 太小时 content 会为空,须给足空间


class TikuDeepSeek(Tiku):
    def __init__(self):
        super().__init__()
        self.name = "DeepSeek 题库"
        self.api = DEEPSEEK_BASE
        self._api_key = None

    def _init_tiku(self):
        # 密钥:环境变量(云端)优先,回退配置文件(本地 config.ini,已被 .gitignore 忽略)
        self._api_key = os.environ.get("DEEPSEEK_API_KEY") or self._conf.get("api_key", "").strip()
        if not self._api_key:
            logger.error("未配置 DeepSeek API Key:请设置环境变量 DEEPSEEK_API_KEY(云端 Secrets)或 config.ini 的 [tiku] api_key(本地)")
            raise Exception("缺少 DeepSeek API Key,请在 config.ini 的 [tiku] 段填写 api_key")

    def _query(self, q_info: dict):
        images = q_info.get("images") or []
        # 图片题:先用视觉模型把图片识别为文字(仅提取文字,不答题)
        image_text = ""
        if images:
            image_text = self._ocr_images(images)
            if not image_text:
                logger.warning("图片识别文字失败,将仅凭题目文字作答")
        # 统一用文本 flash 模型推理答案(禁止使用 PRO 模型)
        content = [{"type": "text", "text": self._build_prompt(q_info, image_text)}]
        answer_text = self._ask_with_retry(TEXT_MODEL, content)
        if not answer_text:
            # ① 常规推理无答案 → 让 DeepSeek 联网搜索,给出合理答案
            logger.warning("DeepSeek 常规推理无答案,尝试联网搜索...")
            answer_text = self._ask_with_web_search(content)
        if not answer_text:
            # ② 联网搜索仍无答案 → 给一个"最接近"的答案(宽松再问一次)
            logger.warning("联网搜索也无答案,尝试最接近答案...")
            answer_text = self._ask_fallback(TEXT_MODEL, content)
        if not answer_text:
            # ③ 三级均失败:返回 None(由 study_work 用最接近兜底,保证提交不失败)
            logger.error(f"DeepSeek 推理/联网/最接近均无答案: {q_info.get('title','')}")
            return None
        logger.info(f"{self.name} 回答: {answer_text}")
        return self._parse_answer(answer_text, q_info.get("type"))

    def _ask_fallback(self, model: str, content: list, retries: int = 3) -> str:
        """最接近答案:用更高温度再问一次 DeepSeek,要求选出最可能正确的答案。
        仅当推理与联网搜索都失败时调用;仍无有效答案返回空串。"""
        for attempt in range(retries):
            result = self._call_api(model, content, temperature=0.9)
            if result is not None:
                text = (result["choices"][0]["message"].get("content") or "").strip()
                if text and text.replace('*', '').strip() != '':
                    logger.info(f"最接近答案: {text}")
                    return text
            if attempt < retries - 1:
                time.sleep(2)
        return ""

    def _ask_with_web_search(self, content: list) -> str:
        """DeepSeek 原生联网搜索:用 Anthropic 兼容 Messages API + web_search_20250305 服务器工具。
        让 DeepSeek 联网检索后给出答案;返回非空答案文本,否则返回空串。"""
        payload = {
            "model": WEB_SEARCH_MODEL,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": content}],
            "tools": [{"type": "web_search_20250305"}],
        }
        try:
            resp = requests.post(
                f"{WEB_SEARCH_BASE}/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=180,
                verify=False,
            )
        except Exception as e:
            logger.error(f"DeepSeek 联网搜索请求异常: {type(e).__name__}: {e}")
            return ""
        if resp.status_code != 200:
            logger.error(f"DeepSeek 联网搜索失败 [{resp.status_code}]: {resp.text[:300]}")
            return ""
        try:
            data = resp.json()
        except Exception:
            logger.error(f"DeepSeek 联网搜索响应解析失败: {resp.text[:300]}")
            return ""
        texts = []
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        answer_text = "".join(texts).strip()
        logger.info(f"DeepSeek 联网搜索 回答: {answer_text}")
        return answer_text

    def _ask_with_retry(self, model: str, content: list, retries: int = 5) -> str:
        """调用 DeepSeek,回答为空或失败时自动重试(限流/服务错误);
        重试时温度递增提高回答命中率。返回非空 content,否则返回空串。"""
        temps = [0.1, 0.2, 0.3, 0.4, 0.5, 0.7]
        for attempt in range(retries):
            temperature = temps[min(attempt, len(temps) - 1)]
            result = self._call_api(model, content, temperature)
            if result is not None:
                text = (result["choices"][0]["message"].get("content") or "").strip()
                # 剔除无意义占位(如 ***、****、... 等),视为无答案,触发重试/联网搜索
                if text and text.replace('*', '').replace('。', '').replace('.', '').strip() == '':
                    text = ''
                if text:
                    return text
            if attempt < retries - 1:
                wait = min(2 * (attempt + 1), 30)
                logger.warning(f"DeepSeek 无答案/失败,重试({attempt + 1}/{retries}),等待{wait}s...")
                time.sleep(wait)
        return ""

    def _ocr_images(self, images: list) -> str:
        """
        调用视觉模型(deepseek-v4-flash-vision-exp)把图片识别为文字。
        仅做图片 -> 文字 的提取,不参与答题推理。
        图片 URL 直传失败时,回退为下载图片转 base64 内联重试一次。
        返回识别出的文字;失败返回空字符串。
        """
        prompt = "请识别图片中的全部文字内容,原样输出。如果图片中没有文字,只输出:无"
        content = [{"type": "text", "text": prompt}]
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": img}})

        result = self._call_api(VISION_MODEL, content)

        if result is None:
            # URL 直传失败(防盗链/不可访问) -> 下载图片转 base64 内联重试一次
            logger.warning("图片 URL 直传失败,尝试下载图片转 base64 重试...")
            content = [{"type": "text", "text": prompt}]
            all_ok = True
            for img in images:
                b64 = self._fetch_image_base64(img)
                if b64:
                    content.append({"type": "image_url", "image_url": {"url": b64}})
                else:
                    all_ok = False
            if all_ok:
                result = self._call_api(VISION_MODEL, content)

        if result is None:
            return ""
        text = result["choices"][0]["message"]["content"].strip()
        logger.info(f"图片识别出的文字: {text}")
        return text if text and text != "无" else ""

    def _build_prompt(self, q_info: dict, image_text: str = "") -> str:
        """把题目与选项(含图片识别出的文字)拼成纯文本 prompt,要求模型只输出答案"""
        q_type = q_info.get("type", "unknown")
        lines = [
            "你是学习通测验答题助手。请根据题目内容选出正确答案。",
            "只输出答案本身,不要输出任何解释、标点或多余文字。",
            f"题型: {q_type}",
            f"题目: {q_info.get('title', '')}",
        ]
        if image_text:
            lines.append(f"题目图片中识别出的文字内容:\n{image_text}")
        options = q_info.get("options") or ""
        if options:
            lines.append("选项:\n" + options)
        lines.append(
            "输出要求: 必须给出一个确定答案,即使不确定也请选出最可能的一项,"
            "严禁输出“无法确定”、“不清楚”或留空;"
            "单选题只输出一个字母(如 A);"
            "多选题输出多个字母并用英文逗号分隔(如 A,C);"
            "判断题输出“正确”或“错误”。"
        )
        return "\n".join(lines)

    def _call_api(self, model: str, content: list, temperature: float = 0.1) -> dict:
        """调用 DeepSeek Chat Completions(OpenAI 兼容),成功返回响应 dict,失败/限流返回 None(由外层重试处理)"""
        try:
            resp = requests.post(
                f"{DEEPSEEK_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": MAX_TOKENS,
                    "temperature": temperature,
                },
                timeout=TIMEOUT,
                verify=False,
            )
        except Exception as e:
            logger.error(f"{self.name} 请求异常: {type(e).__name__}: {e}")
            return None
        if resp.status_code == 429:
            # 触发限流,等待较长时间后由外层重试
            logger.warning(f"{self.name} 触发限流(429),等待 30s 后由重试逻辑重试")
            time.sleep(30)
            return None
        if resp.status_code != 200:
            logger.error(f"{self.name} 请求失败 [{resp.status_code}]: {resp.text[:300]}")
            return None
        try:
            return resp.json()
        except Exception:
            logger.error(f"{self.name} 响应解析失败: {resp.text[:300]}")
            return None

    def _fetch_image_base64(self, url: str):
        """带学习通会话 cookies 下载图片并转为 base64 data URL(防盗链兜底)"""
        try:
            from api.cookies import use_cookies
            resp = requests.get(
                url,
                timeout=30,
                verify=False,
                cookies=use_cookies(),
                headers={"Referer": "https://mooc1.chaoxing.com/"},
            )
            if resp.status_code == 200:
                b64 = base64.b64encode(resp.content).decode("utf-8")
                mime = resp.headers.get("Content-Type", "image/jpeg").split(";")[0] or "image/jpeg"
                return f"data:{mime};base64,{b64}"
            logger.error(f"图片下载失败 [{resp.status_code}]: {url}")
        except Exception as e:
            logger.error(f"图片下载异常: {url} -> {type(e).__name__}: {e}")
        return None

    def _parse_answer(self, answer: str, q_type: str) -> str:
        """把模型输出规整成 answer.py / study_work 能直接使用的答案格式"""
        answer = answer.strip()
        # 去掉常见前缀与空白标点
        for prefix in ("答案：", "答案:", "答案", "正确答案", "正确选项", "选择", "选"):
            answer = answer.replace(prefix, "")
        answer = answer.strip(" ：:。.,，、;；\"'`")

        if q_type == "judgement":
            # 判断题选项 A=对/正确, B=错/错误;DeepSeek 可能返回 A/B 或中文,统一映射到 true/false 词表
            if answer.upper() in ("A", "TRUE", "T", "RIGHT", "YES", "正确", "对", "√", "是"):
                return "正确"
            if answer.upper() in ("B", "FALSE", "F", "WRONG", "NO", "错误", "错", "×", "否", "不对", "不正确"):
                return "错误"
            # 无法识别的值(如 *** 等)一律视为"错误",避免产生非法答案
            logger.warning(f"判断题答案不识别({answer!r}),默认视为'错误'")
            return "错误"

        if q_type == "multiple":
            # 提取所有 A-H 字母,去重排序,逗号分隔(study_work 的 multi_cut 依赖分隔符)
            letters = sorted(set(ch for ch in answer.upper() if ch in "ABCDEFGH"))
            return ",".join(letters) if letters else answer

        # single / completion / unknown:提取首个选项字母,否则原样返回(填空/无法识别)
        letters = [ch for ch in answer.upper() if ch in "ABCDEFGH"]
        return letters[0] if letters else answer

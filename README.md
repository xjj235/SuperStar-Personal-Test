# SuperStar — 学习通(超星)自动刷课脚本

> 神秘小脚本 / 学习通、超星自动刷课 / 利用 GitHub Actions,无需本地部署

自动完成超星学习通平台课程的视频、音频、文档、章节测验、阅读等任务点,支持本地运行,也支持推送到 GitHub 私有仓库后用 **GitHub Actions 云端自动运行**。

> ⚠️ 免责声明:本脚本仅用于学习研究 Python 逆向与自动化技术。自动刷课、自动答题违反学习通平台服务条款,存在账号被风控/封禁的风险,请勿用于实际课程,使用者自行承担一切后果。

---

## ✨ 功能特性

| 功能 | 说明 |
| ---- | ---- |
| 自动登录 | 手机号 + 密码,AES 加密传输 |
| 视频 / 音频任务 | 模拟观看并上报进度,支持 1~2 倍速 |
| 文档任务 | 自动完成 |
| 阅读任务 | 自动完成(仅完成任务点) |
| 章节测验 | DeepSeek AI 自动答题(仅用 flash 系列模型,不用 PRO):图片题先识别为文字,再统一由 deepseek-v4-flash 推理;只保存不提交,人工核对 |
| 章节解锁 | 章节未开放时自动回滚上一任务重试(最多 3 次) |
| 两种运行方式 | 命令行参数 / 配置文件 |
| 云端运行 | GitHub Actions 免费定时运行,无需本地部署 |

## 📁 目录结构

```
SuperStar/
├── .github/workflows/main.yml   # GitHub Actions 云端刷课配置
├── api/                         # 核心代码
│   ├── base.py                  # Chaoxing 主类:登录/课程/任务点/刷课
│   ├── answer.py                # 题库系统(言溪题库 + 本地缓存)
│   ├── decode.py                # 响应解析
│   ├── cxsecret_font.py         # 学习通加密字体破解
│   └── ...                      # 其他辅助模块
├── resource/
│   └── font_map_table.json      # 字体字形哈希映射表
├── app.py                       # Flask + Celery 任务框架(未接入主流程)
├── config_template.ini          # 配置文件模板(复制为 config.ini 使用)
├── main.py                      # 程序入口
└── requirements.txt             # Python 依赖
```

## 🚀 快速开始

### 方式一:本地运行

1. 安装 Python 3.9+(推荐 3.9~3.12),执行:

   ```bash
   pip install -r requirements.txt
   ```

2. 复制配置模板并填写:

   ```bash
   cp config_template.ini config.ini
   ```

   编辑 `config.ini`,填写你的学习通**手机号、密码、课程ID**。

3. 运行:

   ```bash
   python main.py -c config.ini
   ```

   也可以不用配置文件,直接命令行传参:

   ```bash
   python main.py -u 手机号 -p 密码 -l 课程ID,课程ID2 -s 1.5
   ```

   参数说明:

   | 参数 | 含义 |
   | ---- | ---- |
   | `-c/--config` | 使用配置文件运行 |
   | `-u/--username` | 学习通手机号 |
   | `-p/--password` | 登录密码 |
   | `-l/--list` | 要学习的课程ID列表,逗号分隔 |
   | `-s/--speed` | 视频播放倍速,默认 1,最大 2 |

### 方式二:GitHub Actions 云端运行(无需本地部署,无需本地 config.ini)

1. 将本仓库推送到你的 GitHub **私有仓库**(上传步骤见桌面教程文档 `GitHub上传教程.md`)
2. 在 GitHub 仓库页面配置 **Secrets**(设置 → Secrets and variables → Actions):

   | Secret 名称 | 值 | 必填 |
   | ----------- | -- | ---- |
   | `CX_USERNAME` | 学习通手机号 | ✅ |
   | `CX_PASSWORD` | 学习通密码 | ✅ |
   | `COURSE_LIST` | 课程ID,逗号分隔(如 `244403509,123456`) | ✅ |
   | `DEEPSEEK_API_KEY` | DeepSeek API Key(platform.deepseek.com 申请) | ✅(AI 答题) |
   | `TIKU_PROVIDER` | 固定填 `TikuDeepSeek`(仅支持 DeepSeek 答题) | ✅(AI 答题) |
   | `TIKU_SUBMIT` | `false`=只保存不提交(推荐,人工核对) / `true`=直接提交 | 可选 |

3. **自动运行时间**:代码已配置为**避开 DeepSeek API 高峰期**(北京时间约 09:00-23:00 为 API 高峰,凌晨为低谷),每天在北京时间凌晨 **02:00** 和 **06:30** 自动运行;push 代码到 `main` 分支也会触发一次(用于手动测试)。

## ⚙️ 配置说明(config_template.ini)

```ini
[common]
username = xxx        ; 手机号(必填)
password = xxx        ; 密码(必填)
course_list = xxx     ; 课程ID列表,逗号分隔(选填)
speed = 1             ; 视频倍速(1~2)

[tiku]
provider = TikuDeepSeek  ; 仅支持 DeepSeek AI 答题(flash 系列模型,禁止 PRO)
submit = false           ; true=答完直接提交 / false=只保存不提交(建议保持 false 人工核对)
api_key =                ; DeepSeek API Key(本地运行时填写,云端用 Secrets:DEEPSEEK_API_KEY)
true_list = 正确,对,√,是   ; 判断题"正确"的答案表述
false_list = 错误,错,×,否,不对,不正确 ; 判断题"错误"的答案表述
```

> 💡 答题模型说明:纯文字题直接交给文本 flash 模型 `deepseek-v4-flash`;图片题先用视觉模型 `deepseek-v4-flash-vision-exp` 把图片**识别为文字**,再连同题目一起交给 `deepseek-v4-flash` 推理答案。全程不使用 PRO 模型。

> 💡 章节需要"提交测验"才能解锁时,必须把 `submit` 设为 `true`。

## 🔒 安全提醒

- **绝不提交** `config.ini`(含账号密码)、`cookies.txt`、`cache.json` 到仓库——`.gitignore` 已自动忽略
- GitHub Actions 中的账号密码务必使用 **Secrets**,不要写死在代码或 workflow 文件里
- 建议将仓库设为 **Private(私有)**,避免代码与配置泄露

## 📄 文档

- 新手上传教程:见桌面文档 `GitHub上传教程.md`(不随仓库上传)

## 🙏 致谢

- [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing)
- [SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy](https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy)
- [DeepSeek 开放平台](https://platform.deepseek.com/)

> 当前使用学习通账号: [ACCOUNT-HIDDEN] 运行(最新配置)


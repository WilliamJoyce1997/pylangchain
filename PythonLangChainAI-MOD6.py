# coding: utf-8
from flask import Flask, render_template, request, send_file
from langchain_deepseek import ChatDeepSeek
import requests, re, traceback
import os, sys, subprocess
import uuid
import urllib.parse
from langchain_community.agent_toolkits.file_management.toolkit import FileManagementToolkit
from langchain.agents import AgentType, initialize_agent
from langchain.tools import Tool

import base64
import time
import json

app = Flask(__name__, template_folder='.')
ALLOWED_MEDIA_DIRS = set()

URL_RE = re.compile(r'https?://[^\s\'\"<>\)\]\[]+', re.IGNORECASE)
def extract_urls(text, max_urls=5):
    matches = URL_RE.findall(text or "")
    seen, out = set(), []
    for u in matches:
        if u not in seen:
            seen.add(u); out.append(u)
        if len(out) >= max_urls:
            break
    return out

def metaso_reader_single(url, api_key):
    try:
        reader_url = "https://metaso.cn/api/v1/reader"
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "text/plain", "Content-Type": "application/json"}
        payload = {"url": url, "format": "markdown"}
        r = requests.post(reader_url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        content = r.text or ""
        if not content.strip():
            return f"【警告】获取到的内容为空，URL: {url}"
        return content
    except Exception as e:
        return f"【错误】处理URL时出错: {e}"

def metaso_search_and_reader(query, api_key):
    try:
        resp = requests.post(
            "https://metaso.cn/api/v1/search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"q": query, "scope": "webpage", "size": 20, "includeSummary": False, "includeRawContent": False, "format": "chat_completions"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        webpages = data.get("webpages", []) or []
        filtered = [w for w in webpages if "bilibili.com" not in (w.get("link") or "").lower()]
        top_links = filtered[:10]
        results_text = "【联网搜索结果】\n"
        for i, item in enumerate(top_links, 1):
            link = item.get("link", "")
            title = item.get("title", "无标题")
            date = item.get("date", "")
            results_text += f"\n{i}. 标题: {title}\n日期: {date}\n来源: {link}\n正文:\n"
            if link:
                content = metaso_reader_single(link, api_key)
                results_text += content + "\n"
        return results_text
    except Exception as e:
        return f"【错误】搜索失败: {e}"

def make_safe_path(base_dir: str, path: str) -> str:
    if not path:
        raise ValueError("缺少要访问的路径")
    base_abs = os.path.abspath(os.path.expanduser(base_dir))
    target = os.path.abspath(os.path.expanduser(path))
    try:
        common = os.path.commonpath([base_abs, target])
    except ValueError:
        candidate = os.path.abspath(os.path.join(base_abs, path))
        target = candidate
        common = os.path.commonpath([base_abs, target])
    if common != base_abs:
        candidate = os.path.abspath(os.path.join(base_abs, path))
        if os.path.commonpath([base_abs, candidate]) == base_abs:
            target = candidate
        else:
            raise PermissionError(f"禁止访问不在 {base_abs} 下的路径: {target}")
    return target

# Official per-model and per-generation-type schemas, shared with the template.
POLLO_VIDEO_MODELS = {'viduq3-pro': {'label': 'Vidu Q3 Pro',
                'provider': 'vidu',
                'variants': {'image': {'required': ['image'],
                                       'properties': {'prompt': {'type': 'string', 'maxLength': 5000},
                                                      'image': {'type': 'string'},
                                                      'imageTail': {'type': 'string'},
                                                      'duration': {'type': 'integer',
                                                                   'minimum': 1,
                                                                   'maximum': 16,
                                                                   'default': 5},
                                                      'resolution': {'type': 'string',
                                                                     'enum': ['360p',
                                                                              '540p',
                                                                              '720p',
                                                                              '1080p',
                                                                              '2K'],
                                                                     'default': '360p'},
                                                      'generateAudio': {'type': 'boolean', 'default': True},
                                                      'seed': {'type': 'integer',
                                                               'minimum': 0,
                                                               'maximum': 2147483647}}},
                             'text': {'required': ['prompt'],
                                      'properties': {'prompt': {'type': 'string',
                                                                'maxLength': 2000,
                                                                'minLength': 1},
                                                     'aspectRatio': {'type': 'string',
                                                                     'enum': ['16:9', '9:16', '1:1', '4:3', '3:4'],
                                                                     'default': '16:9'},
                                                     'duration': {'type': 'integer',
                                                                  'minimum': 1,
                                                                  'maximum': 16,
                                                                  'default': 5},
                                                     'resolution': {'type': 'string',
                                                                    'enum': ['360p',
                                                                             '540p',
                                                                             '720p',
                                                                             '1080p',
                                                                             '2K'],
                                                                    'default': '360p'},
                                                     'generateAudio': {'type': 'boolean', 'default': True},
                                                     'seed': {'type': 'integer',
                                                              'minimum': 0,
                                                              'maximum': 2147483647}}}}},
 'kling-v3': {'label': 'Kling 3.0',
              'provider': 'kling-ai',
              'variants': {'image': {'required': ['image'],
                                     'properties': {'prompt': {'type': 'string', 'maxLength': 2500},
                                                    'negativePrompt': {'type': 'string', 'maxLength': 2500},
                                                    'image': {'type': 'string'},
                                                    'imageTail': {'type': 'string'},
                                                    'duration': {'type': 'integer',
                                                                 'minimum': 3,
                                                                 'maximum': 15,
                                                                 'default': 5},
                                                    'mode': {'type': 'string',
                                                             'enum': ['std', 'pro', '4K'],
                                                             'default': 'std'},
                                                    'generateAudio': {'type': 'boolean', 'default': True}}},
                           'text': {'required': ['prompt'],
                                    'properties': {'prompt': {'type': 'string', 'maxLength': 2500, 'minLength': 1},
                                                   'negativePrompt': {'type': 'string', 'maxLength': 2500},
                                                   'aspectRatio': {'type': 'string',
                                                                   'enum': ['16:9', '9:16', '1:1'],
                                                                   'default': '16:9'},
                                                   'duration': {'type': 'integer',
                                                                'minimum': 3,
                                                                'maximum': 15,
                                                                'default': 5},
                                                   'mode': {'type': 'string',
                                                            'enum': ['std', 'pro', '4K'],
                                                            'default': 'std'},
                                                   'generateAudio': {'type': 'boolean', 'default': True}}}}},
 'pollo-v2-5': {'label': 'Pollo 2.5',
                'provider': 'pollo-ai',
                'variants': {'image': {'required': ['image'],
                                       'properties': {'prompt': {'type': 'string', 'maxLength': 2000},
                                                      'image': {'type': 'string'},
                                                      'duration': {'type': 'number',
                                                                   'enum': [4, 5, 6, 7, 8, 9, 10, 11, 12, 15],
                                                                   'default': 5},
                                                      'resolution': {'type': 'string',
                                                                     'enum': ['720p', '1080p'],
                                                                     'default': '720p'},
                                                      'mode': {'type': 'string',
                                                               'enum': ['basic', 'pro'],
                                                               'default': 'basic'},
                                                      'generateAudio': {'type': 'boolean', 'default': True}}},
                             'text': {'required': ['prompt'],
                                      'properties': {'prompt': {'type': 'string',
                                                                'maxLength': 2000,
                                                                'minLength': 1},
                                                     'duration': {'type': 'number',
                                                                  'enum': [4, 5, 6, 7, 8, 9, 10, 11, 12, 15],
                                                                  'default': 5},
                                                     'resolution': {'type': 'string',
                                                                    'enum': ['720p', '1080p'],
                                                                    'default': '720p'},
                                                     'aspectRatio': {'type': 'string',
                                                                     'enum': ['16:9', '9:16'],
                                                                     'default': '16:9'},
                                                     'mode': {'type': 'string',
                                                              'enum': ['basic', 'pro'],
                                                              'default': 'basic'},
                                                     'generateAudio': {'type': 'boolean', 'default': True}}}}},
 'veo-3-1': {'label': 'Google Veo 3.1',
             'provider': 'google',
             'variants': {'image': {'required': ['image'],
                                    'properties': {'prompt': {'type': 'string', 'maxLength': 2000},
                                                   'image': {'type': 'string'},
                                                   'imageTail': {'type': 'string'},
                                                   'aspectRatio': {'type': 'string',
                                                                   'enum': ['16:9', '9:16'],
                                                                   'default': '16:9'},
                                                   'duration': {'type': 'number', 'enum': [4, 6, 8], 'default': 4},
                                                   'resolution': {'type': 'string',
                                                                  'enum': ['720p', '1080p', '4K'],
                                                                  'default': '720p'},
                                                   'seed': {'type': 'integer',
                                                            'minimum': 0,
                                                            'maximum': 2147483647},
                                                   'generateAudio': {'type': 'boolean', 'default': True}}},
                          'text': {'required': ['prompt'],
                                   'properties': {'prompt': {'type': 'string', 'maxLength': 2000, 'minLength': 1},
                                                  'aspectRatio': {'type': 'string',
                                                                  'enum': ['16:9', '9:16'],
                                                                  'default': '16:9'},
                                                  'duration': {'type': 'number', 'enum': [4, 6, 8], 'default': 4},
                                                  'resolution': {'type': 'string',
                                                                 'enum': ['720p', '1080p', '4K'],
                                                                 'default': '720p'},
                                                  'seed': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647},
                                                  'generateAudio': {'type': 'boolean', 'default': True}}},
                          'reference': {'required': ['refs', 'prompt'],
                                        'properties': {'refs': {'type': 'array', 'maxItems': 3},
                                                       'prompt': {'type': 'string',
                                                                  'maxLength': 2000,
                                                                  'minLength': 1},
                                                       'duration': {'type': 'number', 'enum': [8], 'default': 8},
                                                       'resolution': {'type': 'string',
                                                                      'enum': ['720p', '1080p'],
                                                                      'default': '720p'},
                                                       'aspectRatio': {'type': 'string',
                                                                       'enum': ['16:9', '9:16'],
                                                                       'default': '16:9'},
                                                       'seed': {'type': 'integer',
                                                                'minimum': 0,
                                                                'maximum': 2147483647},
                                                       'generateAudio': {'type': 'boolean', 'default': True}}}}}}
POLLO_API_BASE = "https://pollo.ai/api/platform/v1"

POLLO_OPTION_LABELS = {
    "duration": "时长（秒）", "resolution": "分辨率", "mode": "生成模式",
    "aspectRatio": "宽高比", "generateAudio": "生成音频", "seed": "随机种子 Seed",
    "negativePrompt": "负面提示词", "imageTail": "尾帧图片 URL",
    "refs": "参考图片 URL（每行一张，最多三张）",
}
POLLO_OPTION_FIELDS = tuple(POLLO_OPTION_LABELS)
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pollo_video_pricing.json"), encoding="utf-8") as pricing_file:
        POLLO_VIDEO_PRICING = json.load(pricing_file)
except (OSError, ValueError):
    POLLO_VIDEO_PRICING = {"rows": [], "read_at": "未读取", "source": "https://api.pollo.ai/zh/pricing"}


def _validate_pollo_url(value, label):
    try:
        parts = urllib.parse.urlsplit(value)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname) and not any(c.isspace() for c in value)
        parts.port  # Also reject malformed port numbers.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{label} 必须是有效的 HTTP(S) 地址")
    return value


def build_pollo_video_request(model, form, require_content=True):
    model_spec = POLLO_VIDEO_MODELS.get(model)
    if not model_spec:
        raise ValueError("不支持的视频模型，请选择列表中的模型")
    prompt = form.get("video_prompt", "").strip()
    image = form.get("video_image_url", "").strip()
    kind = form.get("video_generation_type", "auto").strip() or "auto"
    if kind == "auto":
        kind = "reference" if form.get("video_refs", "").strip() else ("image" if image else "text")
    variant = model_spec["variants"].get(kind)
    if not variant:
        raise ValueError("当前模型不支持所选生成方式")
    properties = variant["properties"]
    video_input = {}
    if prompt:
        video_input["prompt"] = prompt
    if image:
        if "image" not in properties:
            raise ValueError("当前生成方式不接受首帧图片，请使用对应图片输入")
        video_input["image"] = _validate_pollo_url(image, "首帧图片 URL")
    for name in POLLO_OPTION_FIELDS:
        raw = form.get("video_" + name, "").strip()
        if not raw:
            continue
        spec = properties.get(name)
        if not spec:
            raise ValueError(f"当前模型或生成方式不支持 {POLLO_OPTION_LABELS[name]}")
        if spec["type"] == "boolean":
            if raw not in ("true", "false"):
                raise ValueError(f"{POLLO_OPTION_LABELS[name]} 请选择开启或关闭")
            value = raw == "true"
        elif spec["type"] in ("integer", "number"):
            if not re.fullmatch(r"[0-9]+", raw):
                raise ValueError(f"{POLLO_OPTION_LABELS[name]} 必须为整数")
            value = int(raw)
            if "minimum" in spec and value < spec["minimum"] or "maximum" in spec and value > spec["maximum"]:
                raise ValueError(f"{POLLO_OPTION_LABELS[name]} 超出模型允许范围")
        elif name == "refs":
            urls = [url.strip() for url in raw.splitlines() if url.strip()]
            if not 1 <= len(urls) <= spec["maxItems"]:
                raise ValueError("参考图片须为 1–3 张，每行填写一个 URL")
            value = [{"url": _validate_pollo_url(url, "参考图片 URL"), "type": "image"} for url in urls]
        else:
            value = raw
            if name == "imageTail":
                value = _validate_pollo_url(value, "尾帧图片 URL")
        video_input[name] = value
    for name, value in video_input.items():
        spec = properties[name]
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{POLLO_OPTION_LABELS.get(name, name)} 不在模型支持的选项中")
        if isinstance(value, str) and "maxLength" in spec and len(value) > spec["maxLength"]:
            raise ValueError(f"{POLLO_OPTION_LABELS.get(name, '提示文本')} 最多允许 {spec['maxLength']} 个字符")
    if require_content:
        for name in variant["required"]:
            if not video_input.get(name):
                raise ValueError(f"当前生成方式必须填写 {POLLO_OPTION_LABELS.get(name, {'prompt': '提示文本', 'image': '首帧图片 URL'}.get(name, name))}")
    defaults = {name: spec["default"] for name, spec in properties.items() if "default" in spec}
    effective = {**defaults, **video_input}
    if model == "pollo-v2-5" and effective["duration"] == 15 and not effective["generateAudio"]:
        raise ValueError("Pollo 2.5 的 15 秒视频必须开启音频")
    payload = {"input": video_input}
    webhook = form.get("video_webhookUrl", "").strip()
    if webhook:
        payload["webhookUrl"] = _validate_pollo_url(webhook, "回调 URL")
    return payload, kind


def estimate_pollo_video_price(model, kind, video_input):
    from decimal import Decimal
    properties = POLLO_VIDEO_MODELS[model]["variants"][kind]["properties"]
    effective = {name: spec["default"] for name, spec in properties.items() if "default" in spec}
    effective.update(video_input)
    generation_type = {"text": "text to video", "image": "image to video", "reference": "reference to video"}[kind]
    matches = []
    for row in POLLO_VIDEO_PRICING.get("rows", []):
        if row["aliasModelName"] != model or row["generationType"] != generation_type:
            continue
        if (row["mode"] or "").lower() != str(effective.get("mode", "")).lower():
            continue
        if (row["resolution"] or "").lower() != str(effective.get("resolution", "")).lower():
            continue
        if row["audio"] != effective["generateAudio"]:
            continue
        if row["pricingFormula"] == "fixed" and row["length"] != effective["duration"]:
            continue
        matches.append(row)
    result = {"usd": None, "message": "当前组合暂无可验证的 Pollo 价格（未公布或配置未列出）",
              "read_at": POLLO_VIDEO_PRICING.get("read_at", "未读取"),
              "source": POLLO_VIDEO_PRICING.get("source", "https://api.pollo.ai/zh/pricing")}
    if len(matches) != 1 or not matches[0]["apiPlatformPrice"]:
        return result
    row = matches[0]
    rate = Decimal(str(row["apiPlatformPrice"]))
    if row["pricingFormula"] == "fixed":
        amount = rate
        unit = "美元/次生成"
    elif row["pricingFormula"] == "perOutputSecond":
        amount = rate * Decimal(str(effective["duration"]))
        unit = "美元/输出秒"
    else:
        return result
    result.update({"usd": float(amount), "rate_usd": float(rate), "unit": unit,
                   "message": f"预估费用：US$ {amount:.4f} / 次生成（单价 US$ {rate}，{unit}）"})
    return result


@app.route("/video/estimate", methods=["POST"])
def video_estimate():
    model = request.form.get("video_model", "").strip() or "pollo-v2-5"
    try:
        payload, kind = build_pollo_video_request(model, request.form, require_content=False)
        return estimate_pollo_video_price(model, kind, payload["input"])
    except ValueError as error:
        return {"usd": None, "message": str(error),
                "read_at": POLLO_VIDEO_PRICING.get("read_at", "未读取")}, 400


# helpers for Pollo / Ark responses (robust extraction)
def _extract_task_id_from_resp(resp_json):
    if not isinstance(resp_json, dict):
        return (None, None, resp_json)
    if "taskId" in resp_json:
        return (resp_json.get("taskId"), resp_json.get("status"), resp_json)
    data = resp_json.get("data") or resp_json.get("result") or resp_json.get("payload")
    if isinstance(data, dict) and "taskId" in data:
        return (data.get("taskId"), data.get("status"), resp_json)
    for k in ("data","result","payload"):
        node = resp_json.get(k)
        if isinstance(node, dict) and "taskId" in node:
            return (node.get("taskId"), node.get("status"), resp_json)
    return (None, None, resp_json)

def _extract_generations_from_status_resp(st_json):
    if not isinstance(st_json, dict):
        return []
    if "generations" in st_json and isinstance(st_json["generations"], list):
        return st_json["generations"]
    data = st_json.get("data") or st_json.get("result")
    if isinstance(data, dict) and isinstance(data.get("generations"), list):
        return data.get("generations")
    if "results" in st_json and isinstance(st_json["results"], list):
        return st_json["results"]
    return []

def _extract_video_urls_from_status(st_json):
    urls = []
    gens = _extract_generations_from_status_resp(st_json)
    if isinstance(gens, list):
        for g in gens:
            if isinstance(g, dict):
                for k in ("url","videoUrl","video_url","output","output_url","mediaUrl","media_url","result","result_url"):
                    v = g.get(k)
                    if isinstance(v, str) and v:
                        urls.append(v)
    if not urls and isinstance(st_json, dict):
        data = st_json.get("data") or st_json.get("result") or st_json.get("payload") or st_json
        if isinstance(data, dict):
            for k in ("url","videoUrl","video_url","output","output_url","mediaUrl","media_url","result","result_url"):
                v = data.get(k)
                if isinstance(v, str) and v:
                    urls.append(v)
    if not urls:
        try:
            urls = extract_urls(json.dumps(st_json, ensure_ascii=False))
        except Exception:
            urls = []
    return urls

def _build_media_url(path: str) -> str:
    if not path:
        return ""
    qp = urllib.parse.quote(path)
    return f"/media?path={qp}"

# mapping aspect ratio choice -> recommended high-res size (符合方舟示例与像素区间要求)
_ASPECT_TO_SIZE = {
    "1:1": "2048x2048",
    "2:3": "2048x3072",
    "3:4": "2304x3072",
    "4:3": "3072x2304",
    "9:16": "1728x3072",
    "16:9": "3072x1728"
}

# Ark image generation endpoint (火山方舟)
ARK_IMG_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
TTS_AUDIO_DIR = r"C:\Audio"

@app.route('/', methods=['GET', 'POST'])
def index():
    answer = ""
    form_values = {
        "deepseek_key": "",
        "metaso_key": "",
        "asr_ark_key": "",
        "doubao_token": "",
        "doubao_appid": "",
        "voice_speaker_id": "",
        "voice_language": "cn",
        "tts_speaker_id": "",
        "enable_tts": False,
        "tts_audio_url": "",
        "system_prompt": "你是一个本地部署的人工智能助手。当用户启用联网搜索/读取时，会获得额外的信息用于回答。请优先使用联网获取的信息来回答问题，并注明来源与日期。只有在用户明确需要时，才执行本地文件读写。",
        "deepseek_answer": "",
        "ui_session_id": "",
        "ui_request_id": "",
        "ui_action": "",
        "query": "",
        "enable_network": False,
        "enable_persona": False,
        "persona_path": "",
        "enable_memory": False,
        "memory_path": "C:/Memory",
        "base_dir": "",
        "pollo_key": "",
        "video_model": "pollo-v2-5",
        "video_models": POLLO_VIDEO_MODELS,
        "video_option_labels": POLLO_OPTION_LABELS,
        "video_generation_type": "auto",
        "video_options": {name: "" for name in POLLO_OPTION_FIELDS},
        "video_webhookUrl": "",
        "video_pricing_read_at": POLLO_VIDEO_PRICING.get("read_at", "未读取"),
        "video_prompt": "",
        "video_image_url": "",
        "media_dir": "C:\\Picture&Video",
        # image generation fields
        "ark_key": "",
        "image_prompt": "",
        "img2img_file": "",
        "ark_model": "doubao-seedream-5-0-260128",
        "img_ratio": "1:1",
        "image_media_dir": "C:\\Picture&Video",
        "media_images": [],
        "media_videos": []
    }

    if request.method == 'POST':
        # UI metadata only routes the rendered result; it is never model context.
        ui_session_id = re.sub(r"[^a-zA-Z0-9_-]", "", request.form.get("ui_session_id", ""))[:128]
        ui_request_id = re.sub(r"[^a-zA-Z0-9_-]", "", request.form.get("ui_request_id", ""))[:128] or uuid.uuid4().hex
        ui_action = next((name for name in ("save_settings", "clear_memory", "voice_upload", "check_voice_status", "send_image", "send_video") if name in request.form), "chat")
        # common fields
        deepseek_key = request.form.get('deepseek_key', '').strip()
        metaso_key = request.form.get('metaso_key', '').strip()
        base_dir = request.form.get('base_dir', '').strip()
        user_query = request.form.get('query', '').strip()
        use_network = 'enable_network' in request.form
        use_persona = 'use_persona' in request.form
        use_memory = 'use_memory' in request.form
        memory_path = request.form.get('memory_path', '').strip() or "C:/Memory"
        clear_memory = 'clear_memory' in request.form
        asr_ark_key = request.form.get('asr_ark_key', '').strip()
        doubao_token = request.form.get('doubao_token', '').strip()
        doubao_appid = request.form.get('doubao_appid', '').strip()
        voice_speaker_id = request.form.get('voice_speaker_id', '').strip()
        voice_language = request.form.get('voice_language', '').strip() or "cn"
        tts_speaker_id = request.form.get('tts_speaker_id', '').strip()
        enable_tts = 'enable_tts' in request.form
        system_prompt_input = request.form.get('system_prompt', '').strip()

        # Video inputs; optional settings are validated against the selected model schema.
        pollo_key = request.form.get('pollo_key', '').strip()
        video_model = request.form.get('video_model', '').strip() or "pollo-v2-5"
        video_prompt = request.form.get('video_prompt', '').strip()
        video_image_url = request.form.get('video_image_url', '').strip()
        media_dir = request.form.get('media_dir', '').strip() or r"C:\Picture&Video"

        # image generation (Ark) fields
        ark_key = request.form.get('ark_key', '').strip()  # Ark API Key (火山)
        image_prompt = request.form.get('image_prompt', '').strip()
        img2img_files = request.files.getlist('img2img_file')  # optional
        img_ratio = request.form.get('img_ratio', '').strip() or "1:1"
        image_media_dir = request.form.get('image_media_dir', '').strip() or r"C:\Picture&Video"
        ark_model = request.form.get('ark_model', '').strip() or "doubao-seedream-5-0-260128"
        send_video = 'send_video' in request.form
        send_image = 'send_image' in request.form

        persona_text = ""
        memory_text = ""
        deepseek_answer = ""

        # update form_values for template refill
        form_values.update({
            "ui_session_id": ui_session_id,
            "ui_request_id": ui_request_id,
            "ui_action": ui_action,
            "deepseek_key": deepseek_key,
            "metaso_key": metaso_key,
            "asr_ark_key": asr_ark_key,
            "doubao_token": doubao_token,
            "doubao_appid": doubao_appid,
            "voice_speaker_id": voice_speaker_id,
            "voice_language": voice_language,
            "tts_speaker_id": tts_speaker_id,
            "enable_tts": enable_tts,
            "tts_audio_url": "",
            "system_prompt": system_prompt_input,
            "deepseek_answer": deepseek_answer,
            "base_dir": base_dir,
            "query": user_query,
            "enable_network": use_network,
            "enable_persona": use_persona,
            "enable_memory": use_memory,
            "memory_path": memory_path,
            "pollo_key": pollo_key,
            "video_model": video_model,
            "video_generation_type": request.form.get("video_generation_type", "auto").strip() or "auto",
            "video_options": {name: request.form.get("video_" + name, "").strip() for name in POLLO_OPTION_FIELDS},
            "video_webhookUrl": request.form.get("video_webhookUrl", "").strip(),
            "video_prompt": video_prompt,
            "video_image_url": video_image_url,
            "media_dir": media_dir,
            "ark_key": ark_key,
            "image_prompt": image_prompt,
            "ark_model": ark_model,
            "img_ratio": img_ratio,
            "image_media_dir": image_media_dir,
            "img2img_file": ",".join([getattr(f, "filename", "") for f in img2img_files if getattr(f, "filename", "")]),
            "media_images": [],
            "media_videos": []
        })

        if "save_settings" in request.form:
            # The same form fields are accepted without an LLM call or filesystem write.
            # They remain in the browser form for subsequent ordinary POST requests.
            return {"ok": True, "message": "设置已保存。密钥仅保留在当前页面，不写入浏览器存储。"}

        if 'voice_upload' in request.form:
            voice_file = request.files.get('voice_upload_file')
            if not doubao_token:
                return render_template('main.html', answer="❌ 缺少豆包Token", **form_values)
            if not doubao_appid:
                return render_template('main.html', answer="❌ 缺少豆包AppID", **form_values)
            if not voice_speaker_id:
                return render_template('main.html', answer="❌ 缺少声音ID", **form_values)
            if not voice_file or not getattr(voice_file, "filename", ""):
                return render_template('main.html', answer="❌ 请上传训练音频文件", **form_values)
            ext = os.path.splitext(voice_file.filename)[1].lower().lstrip(".")
            fmt = ext or "wav"
            try:
                audio_bytes = voice_file.read()
                b64 = base64.b64encode(audio_bytes).decode("utf-8")
                lang_map = {"cn": 0, "en": 1, "ja": 2}
                lang_val = lang_map.get(voice_language, 0)
                upload_payload = {
                    "appid": doubao_appid,
                    "speaker_id": voice_speaker_id,
                    "audios": [
                        {
                            "audio_bytes": b64,
                            "audio_format": fmt
                        }
                    ],
                    "source": 2,
                    "language": lang_val,
                    "model_type": 4,
                    "extra_params": json.dumps({"voice_clone_denoise_model_id": ""})
                }
                upload_headers = {
                    "Authorization": f"Bearer; {doubao_token}",
                    "Resource-Id": "seed-icl-2.0",
                    "Content-Type": "application/json"
                }
                resp = requests.post("https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload",
                                     headers=upload_headers, json=upload_payload, timeout=60)
                try:
                    resp.raise_for_status()
                except Exception:
                    try:
                        raw = resp.json()
                        return render_template('main.html', answer=f"❌ 上传失败: {json.dumps(raw, ensure_ascii=False)}", **form_values)
                    except Exception:
                        return render_template('main.html', answer=f"❌ 上传失败，HTTP {resp.status_code}: {resp.text}", **form_values)
                try:
                    data = resp.json()
                    return render_template('main.html', answer=f"✅ 上传成功: {json.dumps(data, ensure_ascii=False)}", **form_values)
                except Exception:
                    return render_template('main.html', answer="✅ 上传成功", **form_values)
            except Exception as e:
                tb = traceback.format_exc()
                return render_template('main.html', answer=f"❌ 上传出错: {e}\n\ntraceback:\n{tb}", **form_values)

        if 'check_voice_status' in request.form:
            if not doubao_token:
                return render_template('main.html', answer="❌ 缺少豆包Token", **form_values)
            if not doubao_appid:
                return render_template('main.html', answer="❌ 缺少豆包AppID", **form_values)
            if not voice_speaker_id:
                return render_template('main.html', answer="❌ 缺少声音ID", **form_values)
            try:
                status_headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer; {doubao_token}",
                    "Resource-Id": "seed-icl-2.0"
                }
                status_payload = {
                    "appid": doubao_appid,
                    "speaker_id": voice_speaker_id
                }
                resp = requests.post("https://openspeech.bytedance.com/api/v1/mega_tts/status",
                                     headers=status_headers, json=status_payload, timeout=30)
                try:
                    resp.raise_for_status()
                except Exception:
                    try:
                        raw = resp.json()
                        return render_template('main.html', answer=f"❌ 查询状态失败: {json.dumps(raw, ensure_ascii=False)}", **form_values)
                    except Exception:
                        return render_template('main.html', answer=f"❌ 查询状态失败，HTTP {resp.status_code}: {resp.text}", **form_values)
                try:
                    data = resp.json()
                    return render_template('main.html', answer=f"✅ 训练状态查询结果:\n{json.dumps(data, ensure_ascii=False, indent=2)}", **form_values)
                except Exception:
                    return render_template('main.html', answer=f"✅ 查询成功(非JSON): {resp.text}", **form_values)
            except Exception as e:
                tb = traceback.format_exc()
                return render_template('main.html', answer=f"❌ 查询出错: {e}\n\ntraceback:\n{tb}", **form_values)

        memory_file_path = ""
        if use_memory or clear_memory:
            try:
                os.makedirs(memory_path, exist_ok=True)
            except Exception as e:
                answer = f"❌ 无法创建或访问本地记忆目录 '{memory_path}' ：{e}"
                return render_template('main.html', answer=answer, **form_values)
            memory_file_path = os.path.join(memory_path, "memory.txt")
            if clear_memory:
                try:
                    with open(memory_file_path, "w", encoding="utf-8") as f:
                        f.write("")
                    answer = "✅ 已清空本地记忆"
                    return render_template('main.html', answer=answer, **form_values)
                except Exception as e:
                    answer = f"❌ 清空本地记忆失败: {e}"
                    return render_template('main.html', answer=answer, **form_values)

        # ----------------- 图片生成（Ark）优先检测并处理 -----------------
        want_image = bool(send_image)

        if want_image:
            if not ark_key:
                answer = "❌ 火山引擎 API Key 不能为空"
                return render_template('main.html', answer=answer, **form_values)
            if not image_prompt:
                answer = "❌ 图像提示文本不能为空"
                return render_template('main.html', answer=answer, **form_values)
            size = _ASPECT_TO_SIZE.get(img_ratio)
            if not size:
                answer = f"❌ 未知的图片比例: {img_ratio}"
                return render_template('main.html', answer=answer, **form_values)
            try:
                os.makedirs(image_media_dir, exist_ok=True)
                ALLOWED_MEDIA_DIRS.add(os.path.abspath(image_media_dir))
            except Exception as e:
                answer = f"❌ 无法创建或访问指定图片保存目录 '{image_media_dir}' ：{e}"
                return render_template('main.html', answer=answer, **form_values)
            payload = {
                "model": ark_model,
                "prompt": image_prompt,
                "size": size,
                "watermark": False
            }
            valid_files = [f for f in img2img_files if getattr(f, "filename", "")]
            if valid_files:
                if len(valid_files) < 1 or len(valid_files) > 14:
                    answer = f"❌ 参考图片数量需为 1~14 张，当前: {len(valid_files)}"
                    return render_template('main.html', answer=answer, **form_values)
                images_b64 = []
                for f in valid_files:
                    try:
                        b = f.read()
                        b64 = base64.b64encode(b).decode('utf-8')
                        images_b64.append(f"data:{f.mimetype};base64,{b64}")
                    except Exception as e:
                        answer = f"❌ 读取上传图片失败: {e}"
                        return render_template('main.html', answer=answer, **form_values)
                payload["image"] = images_b64
            try:
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ark_key}"}
                resp = requests.post(ARK_IMG_URL, headers=headers, json=payload, timeout=60)
                try:
                    resp.raise_for_status()
                except Exception:
                    try:
                        raw = resp.json()
                        return render_template('main.html', answer=f"❌ 图片生成请求失败，返回: {json.dumps(raw, ensure_ascii=False)}", **form_values)
                    except Exception:
                        return render_template('main.html', answer=f"❌ 图片生成请求失败，HTTP {resp.status_code}: {resp.text}", **form_values)
                data = resp.json()
                saved_paths = []
                extracted = []
                if isinstance(data, dict):
                    for k in ("data", "results", "outputs", "generations"):
                        node = data.get(k)
                        if isinstance(node, list):
                            extracted = node
                            break
                        if isinstance(node, dict):
                            if isinstance(node.get("images"), list):
                                extracted = node.get("images")
                                break
                if not extracted:
                    extracted = [data]
                for item in extracted:
                    if not isinstance(item, dict):
                        continue
                    img_url = item.get("url") or item.get("imageUrl") or item.get("output") or item.get("output_url")
                    b64 = item.get("b64") or item.get("b64_json") or item.get("base64")
                    if img_url:
                        try:
                            r = requests.get(img_url, timeout=60)
                            r.raise_for_status()
                            fn = f"img_{int(time.time())}.png"
                            path = os.path.join(image_media_dir, fn)
                            with open(path, "wb") as wf:
                                wf.write(r.content)
                            saved_paths.append(path)
                        except Exception as e:
                            saved_paths.append(f"(未下载) {img_url}")
                    elif b64:
                        try:
                            if b64.startswith("data:"):
                                header, b64data = b64.split(",", 1)
                                content = base64.b64decode(b64data)
                            else:
                                content = base64.b64decode(b64)
                            fn = f"img_{int(time.time())}.png"
                            path = os.path.join(image_media_dir, fn)
                            with open(path, "wb") as wf:
                                wf.write(content)
                            saved_paths.append(path)
                        except Exception as e:
                            saved_paths.append(f"(无法保存base64图像)原因:{e}")
                    else:
                        saved_paths.append(f"(未识别项) {json.dumps(item, ensure_ascii=False)[:400]}")
                media_images = []
                for p in saved_paths:
                    if isinstance(p, str) and p.startswith("(未下载) "):
                        media_images.append(p.replace("(未下载) ", "").strip())
                    elif isinstance(p, str) and os.path.isfile(p):
                        media_images.append(_build_media_url(p))
                form_values["media_images"] = media_images
                if not saved_paths:
                    answer = f"❌ 未能解析图片生成结果，原始返回：\n{json.dumps(data, ensure_ascii=False, indent=2)}"
                    return render_template('main.html', answer=answer, **form_values)
                answer = "✅ 图片生成完成，已保存：\n" + "\n".join(saved_paths)
                return render_template('main.html', answer=answer, **form_values)
            except Exception as e:
                tb = traceback.format_exc()
                answer = f"❌ 图片生成出错: {e}\n\ntraceback:\n{tb}"
                return render_template('main.html', answer=answer, **form_values)

        if send_video:
            if not pollo_key:
                return render_template('main.html', answer="❌ Pollo API Key 不能为空", **form_values)
            try:
                payload, generation_kind = build_pollo_video_request(video_model, request.form)
            except ValueError as error:
                return render_template('main.html', answer=f"❌ {error}", **form_values)
            model_spec = POLLO_VIDEO_MODELS[video_model]
            try:
                headers = {
                    "x-api-key": pollo_key,
                    "Content-Type": "application/json"
                }
                url = f"{POLLO_API_BASE}/generation/{model_spec['provider']}/{video_model}/video"
                resp = requests.post(url, json=payload, headers=headers, timeout=60)
                try:
                    resp.raise_for_status()
                except Exception:
                    try:
                        raw = resp.json()
                        return render_template('main.html', answer=f"❌ 视频生成请求失败，返回: {json.dumps(raw, ensure_ascii=False)}", **form_values)
                    except Exception:
                        return render_template('main.html', answer=f"❌ 视频生成请求失败，HTTP {resp.status_code}: {resp.text}", **form_values)
                try:
                    data = resp.json()
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return render_template('main.html', answer=f"❌ 视频生成请求返回异常: {resp.text}", **form_values)
                task_id, task_status, _ = _extract_task_id_from_resp(data)
                if not task_id:
                    return render_template('main.html', answer=f"❌ 未获取到视频任务ID: {json.dumps(data, ensure_ascii=False)}", **form_values)
                try:
                    os.makedirs(media_dir, exist_ok=True)
                    ALLOWED_MEDIA_DIRS.add(os.path.abspath(media_dir))
                except Exception as e:
                    return render_template('main.html', answer=f"❌ 无法创建或访问媒体保存目录 '{media_dir}' ：{e}", **form_values)
                status_url = f"{POLLO_API_BASE}/generation/{urllib.parse.quote(str(task_id), safe='')}/status"
                max_wait_seconds = 300
                poll_interval_seconds = 5
                deadline = time.time() + max_wait_seconds
                last_json = None
                last_text = ""
                while time.time() < deadline:
                    try:
                        r = requests.get(status_url, headers={"x-api-key": pollo_key}, timeout=30)
                        last_text = r.text or ""
                        try:
                            last_json = r.json()
                        except Exception:
                            last_json = None
                    except Exception:
                        last_json = None
                    if isinstance(last_json, dict):
                        status_node = last_json.get("data") or last_json.get("result") or last_json
                        gens = []
                        if isinstance(status_node, dict) and isinstance(status_node.get("generations"), list):
                            gens = status_node.get("generations")
                        elif isinstance(last_json.get("generations"), list):
                            gens = last_json.get("generations")
                        if gens:
                            for g in gens:
                                if not isinstance(g, dict):
                                    continue
                                status_text = str(g.get("status") or "").lower()
                                if status_text in ("succeed","success","completed","complete","done") and g.get("url"):
                                    urls = _extract_video_urls_from_status(last_json)
                                    if not urls:
                                        return render_template('main.html', answer=f"❌ 已成功生成但未找到视频下载链接: {json.dumps(last_json, ensure_ascii=False)}", **form_values)
                                    video_url = urls[0]
                                    try:
                                        v = requests.get(video_url, timeout=120)
                                        v.raise_for_status()
                                        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', task_id)
                                        filename = f"video_{safe_id}_{int(time.time())}.mp4"
                                        save_path = os.path.join(media_dir, filename)
                                        with open(save_path, "wb") as wf:
                                            wf.write(v.content)
                                        form_values["media_videos"] = [_build_media_url(save_path)]
                                        answer = f"✅ 视频生成完成并已下载：\n{save_path}"
                                        return render_template('main.html', answer=answer, **form_values)
                                    except Exception as e:
                                        return render_template('main.html', answer=f"❌ 下载视频失败: {e}", **form_values)
                                if status_text in ("failed","error"):
                                    return render_template('main.html', answer=f"❌ 视频生成失败: {json.dumps(last_json, ensure_ascii=False)}", **form_values)
                        status_val = ""
                        if isinstance(status_node, dict):
                            status_val = status_node.get("status") or last_json.get("status") or ""
                        status_text = str(status_val).lower()
                        if status_text in ("failed","error"):
                            return render_template('main.html', answer=f"❌ 视频生成失败: {json.dumps(last_json, ensure_ascii=False)}", **form_values)
                    time.sleep(poll_interval_seconds)
                if last_json is not None:
                    urls = _extract_video_urls_from_status(last_json)
                    if urls:
                        video_url = urls[0]
                        try:
                            v = requests.get(video_url, timeout=120)
                            v.raise_for_status()
                            safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', task_id)
                            filename = f"video_{safe_id}_{int(time.time())}.mp4"
                            save_path = os.path.join(media_dir, filename)
                            with open(save_path, "wb") as wf:
                                wf.write(v.content)
                            form_values["media_videos"] = [_build_media_url(save_path)]
                            answer = f"✅ 视频生成完成并已下载：\n{save_path}"
                            return render_template('main.html', answer=answer, **form_values)
                        except Exception as e:
                            return render_template('main.html', answer=f"❌ 下载视频失败: {e}", **form_values)
                    return render_template('main.html', answer=f"❌ 视频生成超时未完成: {json.dumps(last_json, ensure_ascii=False)}", **form_values)
                return render_template('main.html', answer=f"❌ 视频生成超时未完成: {last_text}", **form_values)
            except Exception as e:
                tb = traceback.format_exc()
                answer = f"❌ 视频生成出错: {e}\n\ntraceback:\n{tb}"
                return render_template('main.html', answer=answer, **form_values)

        # 读取 persona 文件（如启用）
        if use_persona:
            persona_file = request.files.get('persona_file')
            if not persona_file or not getattr(persona_file, "filename", ""):
                return render_template('main.html', answer="❌ 请上传角色设定文件", **form_values)
            try:
                persona_bytes = persona_file.read()
                persona_text = persona_bytes.decode("utf-8").strip()
            except Exception as e:
                answer = f"❌ 无法读取角色设定文件: {e}"
                return render_template('main.html', answer=answer, **form_values)

        if use_memory:
            try:
                if os.path.exists(memory_file_path):
                    with open(memory_file_path, encoding='utf-8') as f:
                        memory_text = f.read().strip()
            except Exception as e:
                answer = f"❌ 无法读取记忆文件: {e}"
                return render_template('main.html', answer=answer, **form_values)

        if not deepseek_key:
            answer = "❌ DeepSeek API Key 不能为空"
            return render_template('main.html', answer=answer, **form_values)

        # init LLM
        try:
            llm = ChatDeepSeek(model="deepseek-flash", api_key=deepseek_key)
        except Exception as e:
            answer = f"❌ 初始化 DeepSeek LLM 失败: {e}"
            return render_template('main.html', answer=answer, **form_values)

        # default base_dir
        if not base_dir:
            base_dir = r"C:\Script&File"
        try:
            os.makedirs(base_dir, exist_ok=True)
        except Exception as e:
            answer = f"❌ 无法创建或访问指定目录 '{base_dir}' ：{e}"
            return render_template('main.html', answer=answer, **form_values)

        # FileManagementToolkit + custom tools
        try:
            tk = FileManagementToolkit(root_dir=base_dir)
            file_tools = tk.get_tools()
            def run_executable_tool(path: str) -> str:
                try:
                    safe = make_safe_path(base_dir, path)
                    if not os.path.exists(safe):
                        return f"错误：文件不存在：{safe}"
                    ext = os.path.splitext(safe)[1].lower()
                    if ext not in (".exe", ".bat", ".cmd", ""):
                        return f"错误：目标文件扩展 ({ext}) 非常用可执行类型，拒绝执行。"
                    if sys.platform.startswith("win"):
                        proc = subprocess.run([safe], capture_output=True, text=True, timeout=300, shell=False)
                    else:
                        proc = subprocess.run([safe], capture_output=True, text=True, timeout=300, shell=False)
                    stdout = proc.stdout.strip()
                    stderr = proc.stderr.strip()
                    if proc.returncode == 0:
                        return stdout or f"已成功执行 {safe}，但无输出。"
                    else:
                        return f"程序返回码 {proc.returncode}。stdout:\\n{stdout}\\nstderr:\\n{stderr}"
                except PermissionError as pe:
                    return f"权限错误: {pe}"
                except subprocess.TimeoutExpired:
                    return "错误: 程序执行超时。"
                except Exception as e:
                    return f"执行 .exe 时出错: {e}"

            def run_powershell_tool(path: str) -> str:
                try:
                    safe = make_safe_path(base_dir, path)
                    if not os.path.exists(safe):
                        return f"错误：脚本不存在：{safe}"
                    ext = os.path.splitext(safe)[1].lower()
                    if ext != ".ps1":
                        return f"错误：文件扩展 {ext} 不是 PowerShell 脚本 (.ps1)"
                    if not sys.platform.startswith("win"):
                        return "错误：PowerShell 脚本执行目前仅在 Windows 系统上受支持。"
                    cmd = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", safe]
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, shell=False)
                    stdout = proc.stdout.strip()
                    stderr = proc.stderr.strip()
                    if proc.returncode == 0:
                        return stdout or f"已成功执行 PowerShell 脚本 {safe}，但无输出。"
                    else:
                        return f"脚本返回码 {proc.returncode}。stdout:\\n{stdout}\\nstderr:\\n{stderr}"
                except PermissionError as pe:
                    return f"权限错误: {pe}"
                except subprocess.TimeoutExpired:
                    return "错误: PowerShell 脚本执行超时。"
                except Exception as e:
                    return f"执行 .ps1 时出错: {e}"

            run_exe_tool = Tool(
                name="run_executable",
                func=run_executable_tool,
                description=(
                    "在 AI 可操作根目录内运行指定的可执行文件 (.exe 或 .bat 等)。"
                    "参数为文件路径（相对或绝对），路径必须在已告知的根目录内。"
                    "返回执行的 stdout 或错误信息。"
                )
            )
            run_ps1_tool = Tool(
                name="run_powershell",
                func=run_powershell_tool,
                description=(
                    "在 AI 可操作根目录内运行指定的 PowerShell 脚本 (.ps1)。"
                    "参数为文件路径（相对或绝对），路径必须在已告知的根目录内。"
                    "返回执行的 stdout 或错误信息。"
                )
            )
            selected_tools = file_tools + [run_exe_tool, run_ps1_tool]
            if selected_tools:
                agent_executor = initialize_agent(selected_tools, llm,
                                                  agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
                                                  verbose=False)
            else:
                agent_executor = None
        except Exception as e:
            answer = f"❌ 初始化文件工具失败: {e}"
            return render_template('main.html', answer=answer, **form_values)

        # construct system prompt
        system_prompt = system_prompt_input
        if persona_text:
            system_prompt += "\n根据以下内容严格进行角色扮演：\n" + persona_text

        # extra_context (network)
        extra_context = ""
        if use_network:
            if not metaso_key:
                extra_context = "【提示】已选择启用联网，但未填写秘塔 API Key，联网功能将被跳过。\n"
            else:
                urls = extract_urls(user_query, max_urls=5)
                if urls:
                    extra_context = "【联网读取结果】\n"
                    for i, u in enumerate(urls, 1):
                        content = metaso_reader_single(u, metaso_key)
                        extra_context += f"\n{i}. 来源: {u}\n{content}\n"
                else:
                    extra_context = metaso_search_and_reader(user_query, metaso_key)

        # final input composition
        final_input = ""
        if memory_text:
            final_input += memory_text + "\n\n"
        if persona_text:
            final_input += persona_text + "\n\n"
        final_input += user_query
        if extra_context:
            final_input += "\n\n（以下是联网获取的信息）\n" + extra_context

        final_input = system_prompt + "\n\n" + final_input

        # call agent or LLM
        try:
            if agent_executor:
                answer_text = agent_executor.run(final_input)
            else:
                formatted = [("system", system_prompt), ("human", final_input)]
                resp = llm.invoke([{"role": r, "content": c} for r, c in formatted])
                answer_text = getattr(resp, "content", None) or getattr(resp, "text", None) or str(resp)

            deepseek_answer = answer_text
            form_values["deepseek_answer"] = deepseek_answer
            if extra_context:
                answer = extra_context + "\n\nAI助手：" + answer_text
            else:
                answer = answer_text

            if use_memory and deepseek_answer and memory_file_path:
                try:
                    with open(memory_file_path, "a", encoding="utf-8") as f:
                        f.write(f"USER: {user_query}\nAI: {deepseek_answer}\n\n")
                except Exception as e:
                    answer = answer + f"\n\n⚠️ 本地记忆写入失败: {e}"

            if enable_tts and asr_ark_key and tts_speaker_id and deepseek_answer:
                try:
                    os.makedirs(TTS_AUDIO_DIR, exist_ok=True)
                    tts_payload = {
                        "app": {"cluster": "volcano_icl"},
                        "user": {"uid": "豆包语音"},
                        "audio": {
                            "voice_type": tts_speaker_id,
                            "encoding": "mp3",
                            "speed_ratio": 1.0
                        },
                        "request": {
                            "reqid": uuid.uuid4().hex,
                            "text": deepseek_answer,
                            "operation": "query"
                        }
                    }
                    tts_headers = {
                        "x-api-key": asr_ark_key,
                        "Content-Type": "application/json"
                    }
                    tts_resp = requests.post("https://openspeech.bytedance.com/api/v1/tts",
                                             headers=tts_headers, json=tts_payload, timeout=60)
                    audio_path = ""
                    audio_filename = ""
                    content_type = (tts_resp.headers.get("Content-Type") or "").lower()
                    if "application/json" in content_type:
                        tts_json = tts_resp.json()
                        audio_b64 = ""
                        if isinstance(tts_json, dict):
                            data_node = tts_json.get("data") or tts_json.get("result") or tts_json.get("audio")
                            if isinstance(data_node, dict):
                                audio_b64 = data_node.get("audio") or data_node.get("audio_data") or ""
                            elif isinstance(data_node, str):
                                audio_b64 = data_node
                        if audio_b64:
                            audio_bytes = base64.b64decode(audio_b64)
                            audio_filename = f"tts_{int(time.time())}.mp3"
                            audio_path = os.path.join(TTS_AUDIO_DIR, audio_filename)
                            with open(audio_path, "wb") as wf:
                                wf.write(audio_bytes)
                    elif tts_resp.content:
                        audio_filename = f"tts_{int(time.time())}.mp3"
                        audio_path = os.path.join(TTS_AUDIO_DIR, audio_filename)
                        with open(audio_path, "wb") as wf:
                            wf.write(tts_resp.content)
                    if audio_path:
                        form_values["tts_audio_url"] = f"/tts_audio/{audio_filename}"
                        answer = answer + f"\n\n✅ 语音已保存: {audio_path}"
                    else:
                        try:
                            tts_json = tts_resp.json()
                            answer = answer + f"\n\n⚠️ 语音生成返回: {json.dumps(tts_json, ensure_ascii=False)}"
                        except Exception:
                            answer = answer + f"\n\n⚠️ 语音生成未返回音频，HTTP {tts_resp.status_code}"
                except Exception as e:
                    tb = traceback.format_exc()
                    answer = answer + f"\n\n❌ 语音生成出错: {e}\n\ntraceback:\n{tb}"
        except Exception as e:
            tb = traceback.format_exc()
            answer = f"❌ 处理请求时出错: {e}\n\ntraceback:\n{tb}"

    return render_template('main.html', answer=answer, **form_values)


@app.route('/media')
def media():
    path = request.args.get("path", "")
    if not path:
        return "Not Found", 404
    try:
        target = os.path.abspath(os.path.expanduser(path))
        allowed = [os.path.abspath(p) for p in ALLOWED_MEDIA_DIRS]
        ok = False
        for base in allowed:
            try:
                if os.path.commonpath([base, target]) == base:
                    ok = True
                    break
            except Exception:
                continue
        if not ok:
            return "Forbidden", 403
        if not os.path.exists(target):
            return "Not Found", 404
        return send_file(target)
    except Exception:
        return "Not Found", 404


@app.route('/tts_audio/<filename>')
def tts_audio(filename):
    try:
        os.makedirs(TTS_AUDIO_DIR, exist_ok=True)
        safe_path = make_safe_path(TTS_AUDIO_DIR, filename)
        if not os.path.exists(safe_path):
            return "Not Found", 404
        return send_file(safe_path, mimetype="audio/mpeg")
    except Exception:
        return "Not Found", 404


if __name__ == "__main__":
    # 只在 reloader 的“子进程”中自动打开浏览器，避免 debug 模式下重复打开两次窗口。
    import webbrowser
    from threading import Timer

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        Timer(1, lambda: webbrowser.open('http://127.0.0.1:5000')).start()

    # 开发阶段 debug=True；若不想自动打开浏览器则把 debug=False
    app.run(host='127.0.0.1', port=5000, debug=True)

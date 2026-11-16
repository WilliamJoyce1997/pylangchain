# coding: utf-8
from flask import Flask, render_template, request, send_file, session
from deepseek_extensions import (DeepSeekChat as ChatDeepSeek, DeepSeekAPI, DeepSeekError,
    init_deepseek, deepseek_context, make_deepseek_tools, tool_summary, AGENT_RULES)
import requests, re, traceback
import os, sys, subprocess
import uuid
import urllib.parse
from langchain_community.agent_toolkits.file_management.toolkit import FileManagementToolkit
from langchain.agents import create_agent
from langchain.agents.middleware import after_model
from langchain.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError

import base64
import time
import json

app = Flask(__name__, template_folder='.')
deepseek_state = init_deepseek(app)
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

def make_safe_path(base_dir: str, path: str) -> str:
    if not path:
        raise ValueError("缺少要访问的路径")
    root = os.path.realpath(os.path.expanduser(base_dir))
    target = os.path.realpath(os.path.join(root, os.path.expanduser(path)))
    try:
        inside = os.path.normcase(os.path.commonpath([root, target])) == os.path.normcase(root)
    except ValueError:
        inside = False
    if not inside:
        raise PermissionError(f"禁止访问不在 {root} 下的路径: {target}")
    return target


@after_model
def one_tool_per_step(state, runtime):
    """Keep the previous Agent's one-action steps, including stop-on-delete."""
    message = state["messages"][-1]
    if isinstance(message, AIMessage) and len(message.tool_calls) > 1:
        return {"messages": [message.model_copy(update={"tool_calls": message.tool_calls[:1], "invalid_tool_calls": []})]}
    return None


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


# Nano Banana 2.1: official Pollo schema, also used to render image controls.
NANO_IMAGE_MODEL = "nano-banana-2-1"
NANO_IMAGE_SCHEMA = {
    "maxReferences": 14, "promptMaxLength": 20000,
    "aspectRatio": ["1:1", "9:16", "16:9", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "1:4", "4:1", "8:1", "1:8"],
    "options": {
        "resolution": {"label": "分辨率", "enum": ["1K", "2K", "4K"], "default": "1K"},
        "thinkingLevel": {"label": "思考级别", "enum": ["minimal", "medium", "high"], "default": "minimal"},
    },
}


def build_nano_image_request(form, files=None):
    if any(getattr(f, "filename", "") for f in (files or [])):
        raise ValueError("Nano Banana 2.1 仅接受在线参考图片 URL，不支持本地上传或 Base64")
    prompt = form.get("image_prompt", "").strip()
    if not 1 <= len(prompt) <= NANO_IMAGE_SCHEMA["promptMaxLength"]:
        raise ValueError("Nano Banana 2.1 提示词须为 1–20000 个字符")
    image_input = {"prompt": prompt}
    ratio = form.get("img_ratio", "").strip()
    if ratio:
        if ratio not in NANO_IMAGE_SCHEMA["aspectRatio"]:
            raise ValueError("Nano Banana 2.1 不支持当前图片比例")
        image_input["aspectRatio"] = ratio
    for name, spec in NANO_IMAGE_SCHEMA["options"].items():
        value = form.get("nano_" + name, "").strip()
        if not value:
            continue
        if name == "resolution":
            value = value.upper()
        if value not in spec["enum"]:
            raise ValueError("Nano Banana 2.1 不支持当前" + spec["label"])
        image_input[name] = value
    urls = [line.strip() for line in form.get("nano_images", "").splitlines() if line.strip()]
    if len(urls) > NANO_IMAGE_SCHEMA["maxReferences"]:
        raise ValueError("Nano Banana 2.1 最多支持 14 张参考图，每行一个 URL")
    if urls:
        image_input["images"] = [_validate_pollo_url(url, "参考图片 URL") for url in urls]
    if any(form.get(name, "").strip() for name in ("xai_image_resolution", "xai_image_quality")):
        raise ValueError("Nano Banana 2.1 不支持 xAI 图片参数")
    return {"input": image_input}


def generate_nano_images(key, directory, payload):
    if not key:
        raise ValueError("请在设置中填写 Pollo API Key（供图片/视频平台共用）")
    # Check the destination before submitting a billable generation.
    os.makedirs(directory, exist_ok=True)
    if not os.path.isdir(directory) or not os.access(directory, os.W_OK):
        raise ValueError("无法写入指定图片保存目录")
    ALLOWED_MEDIA_DIRS.add(os.path.abspath(directory))
    headers = {"x-api-key": key, "Content-Type": "application/json"}
    response = requests.post(POLLO_API_BASE + "/generation/google/nano-banana-2-1/image",
                             headers=headers, json=payload, timeout=60)
    if not response.ok:
        raise ValueError(f"图片生成请求失败（HTTP {response.status_code}），请检查密钥、额度及参数")
    task_data = response.json()
    task_id, status, _ = _extract_task_id_from_resp(task_data)
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("图片生成响应缺少任务 ID")
    if status == "failed":
        raise ValueError(f"图片生成失败，任务 ID：{task_id}")
    status_url = POLLO_API_BASE + "/generation/" + urllib.parse.quote(task_id, safe="") + "/status"
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        response = requests.get(status_url, headers={"x-api-key": key}, timeout=30)
        if not response.ok:
            raise ValueError(f"图片任务查询失败（HTTP {response.status_code}），任务 ID：{task_id}")
        generations = _extract_generations_from_status_resp(response.json())
        if any(g.get("status") == "failed" for g in generations):
            raise ValueError(f"图片生成失败，任务 ID：{task_id}")
        if generations and all(g.get("status") == "succeed" for g in generations):
            paths = []
            for generated in generations:
                if generated.get("mediaType") != "image" or not generated.get("url"):
                    raise ValueError(f"任务未返回有效图片，任务 ID：{task_id}")
                url = _validate_pollo_url(generated["url"], "图片下载 URL")
                download = requests.get(url, timeout=120)
                download.raise_for_status()
                _, extension = _xai_image_type(download.content)
                path = os.path.join(directory, "nano_" + uuid.uuid4().hex + extension)
                with open(path, "wb") as output:
                    output.write(download.content)
                paths.append(path)
            return paths
        if any(g.get("status") not in ("waiting", "processing", "succeed") for g in generations):
            raise ValueError(f"图片任务返回未知状态，任务 ID：{task_id}")
        time.sleep(5)
    raise ValueError(f"图片任务等待超时，任务 ID：{task_id}；请勿重复提交同一任务")


# xAI Imagine schemas are also rendered by the frontend. API fields remain provider-specific.
XAI_API_BASE = "https://api.x.ai/v1"
XAI_IMAGE_MODEL = "grok-imagine-image-2.0"
XAI_IMAGE_RATIOS = ["auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "19.5:9", "9:19.5", "20:9", "9:20", "21:9", "5:2"]
XAI_VIDEO_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "5:2"]
XAI_VIDEO_MODELS = {}
for _xai_model in ("grok-imagine-video-1.5", "grok-imagine-video-1.5-lite"):
    _variants = {}
    for _kind in ("text", "image", "reference"):
        if _kind == "reference" and _xai_model.endswith("-lite"):
            continue
        _props = {
            "prompt": {"type": "string"},
            "duration": {"type": "integer", "minimum": 1, "maximum": 15, "default": 8},
            "resolution": {"type": "string", "enum": ["480p", "720p"] if _kind == "reference" else ["480p", "720p", "1080p"], "default": "480p"},
            "generateAudio": {"type": "boolean", "default": True},
        }
        if _kind != "image":
            _props["aspectRatio"] = {"type": "string", "enum": XAI_VIDEO_RATIOS, "default": "16:9"}
        if _kind == "image":
            _props["image"] = {"type": "string"}
        if _kind == "reference":
            _props["refs"] = {"type": "array", "maxItems": 14}
        _variants[_kind] = {"required": ["image"] if _kind == "image" else (["prompt", "refs"] if _kind == "reference" else ["prompt"]), "properties": _props}
    XAI_VIDEO_MODELS[_xai_model] = {"label": _xai_model, "provider": "xai", "variants": _variants}
VIDEO_MODELS = {**POLLO_VIDEO_MODELS, **XAI_VIDEO_MODELS}


def _xai_uploaded_images(files, maximum):
    files = [f for f in (files or []) if getattr(f, "filename", "")]
    if len(files) > maximum:
        raise ValueError(f"当前模型和生成方式最多支持 {maximum} 张输入图片")
    images = []
    for uploaded in files:
        content = uploaded.read()
        mime, _ = _xai_image_type(content)
        images.append("data:" + mime + ";base64," + base64.b64encode(content).decode("ascii"))
    return images


def _xai_image_type(content):
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ValueError("图片须为非空的 PNG、JPEG 或 WebP 文件")


def build_xai_video_request(model, form, files=None, require_content=True):
    if model not in XAI_VIDEO_MODELS:
        raise ValueError("不支持的 xAI 视频模型")
    files = files or {}
    first_files = [f for f in files.getlist("video_image_file") if getattr(f, "filename", "")] if hasattr(files, "getlist") else []
    ref_files = [f for f in files.getlist("video_refs_files") if getattr(f, "filename", "")] if hasattr(files, "getlist") else []
    image = form.get("video_image_url", "").strip()
    refs = [u.strip() for u in form.get("video_refs", "").splitlines() if u.strip()]
    kind = form.get("video_generation_type", "auto").strip() or "auto"
    if kind == "auto":
        kind = "reference" if refs or ref_files else ("image" if image or first_files else "text")
    variant = XAI_VIDEO_MODELS[model]["variants"].get(kind)
    if not variant:
        raise ValueError("当前模型不支持所选生成方式；Lite 仅支持文字或单张首帧图")
    if image and first_files:
        raise ValueError("首帧图片 URL 与上传图片只能选择一种")
    if len(first_files) > 1:
        raise ValueError("图生视频仅支持 1 张首帧图")
    if kind != "image" and (image or first_files):
        raise ValueError("当前生成方式不接受首帧图片")
    if kind != "reference" and (refs or ref_files):
        raise ValueError("当前生成方式不接受多张参考图")
    if len(refs) + len(ref_files) > 14:
        raise ValueError("参考图片总数最多为 14 张（含 URL 与上传文件）")
    props = variant["properties"]
    payload = {"model": model}
    prompt = form.get("video_prompt", "").strip()
    if prompt:
        payload["prompt"] = prompt
    for name in POLLO_OPTION_FIELDS:
        raw = form.get("video_" + name, "").strip()
        if not raw or name == "refs":
            continue
        spec = props.get(name)
        if not spec:
            raise ValueError(f"当前模型或生成方式不支持 {POLLO_OPTION_LABELS[name]}")
        value = raw
        if spec["type"] == "boolean":
            if raw not in ("true", "false"):
                raise ValueError("音频参数必须为 true 或 false")
            value = raw == "true"
        elif spec["type"] == "integer":
            if not re.fullmatch(r"[0-9]+", raw) or not spec["minimum"] <= int(raw) <= spec["maximum"]:
                raise ValueError("视频时长须为 1–15 秒的整数")
            value = int(raw)
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{POLLO_OPTION_LABELS[name]} 不在当前生成方式允许的选项中")
        payload[{"aspectRatio": "aspect_ratio", "generateAudio": "generate_audio"}.get(name, name)] = value
    if form.get("video_webhookUrl", "").strip():
        raise ValueError("xAI 不使用此回调参数")
    if kind == "image":
        urls = _xai_uploaded_images(first_files, 1)
        if image:
            urls.append(_validate_pollo_url(image, "首帧图片 URL"))
        if urls:
            payload["image"] = {"url": urls[0]}
    elif kind == "reference":
        urls = [_validate_pollo_url(u, "参考图片 URL") for u in refs] + _xai_uploaded_images(ref_files, 14)
        if urls:
            payload["reference_images"] = [{"url": u} for u in urls]
    if require_content:
        for required in variant["required"]:
            if not payload.get({"refs": "reference_images"}.get(required, required)):
                raise ValueError("请填写当前生成方式所需的提示文本或图片")
    return payload, kind


def build_xai_image_request(form, files):
    prompt = form.get("image_prompt", "").strip()
    if not prompt:
        raise ValueError("图像提示文本不能为空")
    payload = {"model": XAI_IMAGE_MODEL, "prompt": prompt, "response_format": "b64_json"}
    ratio = form.get("img_ratio", "").strip()
    if ratio:
        if ratio not in XAI_IMAGE_RATIOS:
            raise ValueError("不支持的图片比例")
        payload["aspect_ratio"] = ratio
    for name, allowed in (("resolution", ("1k", "2k")), ("quality", ("auto", "low", "medium"))):
        value = form.get("xai_image_" + name, "").strip()
        if value:
            if value not in allowed:
                raise ValueError("不支持的图片" + name)
            payload[name] = value
    images = _xai_uploaded_images(files, 5)
    endpoint = "/images/generations"
    if images:
        endpoint = "/images/edits"
        if len(images) == 1:
            payload["image"] = {"type": "image_url", "url": images[0]}
        else:
            payload["images"] = [{"type": "image_url", "url": u} for u in images]
    return endpoint, payload


def _xai_json(method, endpoint, key, payload=None):
    response = requests.request(method, XAI_API_BASE + endpoint,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        **({"json": payload} if payload is not None else {}), timeout=180)
    if not response.ok:
        # Do not echo credentials or upstream request payloads into history.
        raise ValueError(f"xAI 请求失败（HTTP {response.status_code}），请检查密钥、额度及输入参数")
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("xAI 返回了无法识别的结果")
    return data


def _save_xai_media(content, directory, extension):
    if not content:
        raise ValueError("生成结果为空，未保存文件")
    path = os.path.join(directory, "grok_" + uuid.uuid4().hex + extension)
    with open(path, "wb") as output:
        output.write(content)
    return path


def generate_xai_media(kind, key, directory, payload, endpoint=None):
    if not key:
        raise ValueError("xAI (Grok) API Key 不能为空")
    # Verify the destination before creating a billable task.
    os.makedirs(directory, exist_ok=True)
    if not os.path.isdir(directory) or not os.access(directory, os.W_OK):
        raise ValueError("无法写入指定的媒体保存目录")
    ALLOWED_MEDIA_DIRS.add(os.path.abspath(directory))
    data = _xai_json("POST", endpoint if kind == "image" else "/videos/generations", key, payload)
    if kind == "image":
        paths = []
        for image in data.get("data", []):
            if image.get("respect_moderation") is False:
                raise ValueError("图片未通过内容审核")
            if image.get("b64_json"):
                content = base64.b64decode(image["b64_json"], validate=True)
            elif image.get("url"):
                download = requests.get(_validate_pollo_url(image["url"], "图片下载地址"), timeout=120)
                download.raise_for_status()
                content = download.content
            else:
                raise ValueError("图片返回结果缺少图片数据")
            _, extension = _xai_image_type(content)
            paths.append(_save_xai_media(content, directory, extension))
        if not paths:
            raise ValueError("xAI 未返回可保存的图片")
        return paths
    task = data.get("request_id")
    if not isinstance(task, str) or not task:
        raise ValueError("xAI 未返回视频任务 ID")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        result = _xai_json("GET", "/videos/" + urllib.parse.quote(task, safe=""), key)
        status = result.get("status")
        if status == "done":
            video = result.get("video") or {}
            if video.get("respect_moderation") is False:
                raise ValueError("视频未通过内容审核")
            url = _validate_pollo_url(video.get("url", ""), "视频下载地址")
            download = requests.get(url, timeout=180)
            download.raise_for_status()
            return [_save_xai_media(download.content, directory, ".mp4")]
        if status in ("failed", "expired"):
            raise ValueError(f"视频任务{status}，任务 ID：{task}")
        if status not in ("pending", "processing", "queued"):
            raise ValueError(f"视频任务返回未知状态，任务 ID：{task}")
        time.sleep(5)
    raise ValueError(f"视频仍在生成，等待已超时。任务 ID：{task}；请勿重复提交同一任务")


@app.route("/video/estimate", methods=["POST"])
def video_estimate():
    model = request.form.get("video_model", "").strip() or "pollo-v2-5"
    try:
        if model in XAI_VIDEO_MODELS:
            build_xai_video_request(model, request.form, require_content=False)
            return {"usd": None, "message": "xAI 费用请以官方价格页及实际结算为准，当前未配置价格快照", "source": "https://docs.x.ai/developers/pricing"}
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
        **deepseek_context(),
        "deepseek_key": "",
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
        "video_models": VIDEO_MODELS,
        "xai_key": "",
        "xai_image_ratios": XAI_IMAGE_RATIOS,
        "nano_image_schema": NANO_IMAGE_SCHEMA,
        "nano_images": "",
        "nano_options": {name: "" for name in NANO_IMAGE_SCHEMA["options"]},
        "xai_image_resolution": "",
        "xai_image_quality": "",
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
        ui_session_id = re.sub(r"[^a-zA-Z0-9_-]", "", request.form.get("ui_session_id", ""))[:128] or uuid.uuid4().hex
        ui_request_id = re.sub(r"[^a-zA-Z0-9_-]", "", request.form.get("ui_request_id", ""))[:128] or uuid.uuid4().hex
        ui_action = next((name for name in ("save_settings", "clear_memory", "voice_upload", "check_voice_status", "send_image", "send_video") if name in request.form), "chat")
        # common fields
        deepseek_key = request.form.get('deepseek_key', '').strip()
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
        xai_key = request.form.get("xai_key", "").strip()
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
            "xai_key": xai_key,
            "xai_image_resolution": request.form.get("xai_image_resolution", "").strip(),
            "xai_image_quality": request.form.get("xai_image_quality", "").strip(),
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
            "nano_images": request.form.get("nano_images", "").strip(),
            "nano_options": {name: request.form.get("nano_" + name, "").strip() for name in NANO_IMAGE_SCHEMA["options"]},
            "img_ratio": img_ratio,
            "image_media_dir": image_media_dir,
            "img2img_file": ",".join([getattr(f, "filename", "") for f in img2img_files if getattr(f, "filename", "")]),
            "media_images": [],
            "media_videos": []
        })

        # Preserve Chat references through unrelated ordinary form submissions.
        if ui_action != "chat" and deepseek_key:
            try:
                saved_chat_ids = json.loads(request.form.get("chat_file_ids", "[]"))
                form_values["chat_attachments"] = deepseek_state.references(
                    session["deepseek_owner"], ui_session_id, deepseek_key, saved_chat_ids)
                form_values["chat_file_ids"] = json.dumps(saved_chat_ids)
            except (ValueError, TypeError):
                pass

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

        if want_image and ark_model == NANO_IMAGE_MODEL:
            try:
                payload = build_nano_image_request(request.form, img2img_files)
                paths = generate_nano_images(pollo_key, image_media_dir, payload)
                form_values["media_images"] = [_build_media_url(path) for path in paths]
                return render_template('main.html', answer="✅ 图片生成完成，已保存：\n" + "\n".join(paths), **form_values)
            except Exception as error:
                return render_template('main.html', answer=f"❌ 图片生成失败：{error}", **form_values)

        if want_image and ark_model == XAI_IMAGE_MODEL:
            try:
                endpoint, payload = build_xai_image_request(request.form, img2img_files)
                paths = generate_xai_media("image", xai_key, image_media_dir, payload, endpoint)
                form_values["media_images"] = [_build_media_url(path) for path in paths]
                return render_template('main.html', answer="✅ 图片生成完成，已保存：\n" + "\n".join(paths), **form_values)
            except Exception as error:
                return render_template('main.html', answer=f"❌ 图片生成失败：{error}", **form_values)

        if want_image:
            if ark_model not in ("doubao-seedream-5-0-260128", "doubao-seedream-5-0-pro-260628"):
                return render_template('main.html', answer="❌ 不支持的图片模型", **form_values)
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

        if send_video and video_model in XAI_VIDEO_MODELS:
            try:
                payload, generation_kind = build_xai_video_request(video_model, request.form, request.files)
                paths = generate_xai_media("video", xai_key, media_dir, payload)
                form_values["media_videos"] = [_build_media_url(path) for path in paths]
                return render_template('main.html', answer="✅ 视频生成完成，已保存：\n" + "\n".join(paths), **form_values)
            except Exception as error:
                return render_template('main.html', answer=f"❌ 视频生成失败：{error}", **form_values)

        if send_video:
            if any(getattr(f, "filename", "") for name in ("video_image_file", "video_refs_files") for f in request.files.getlist(name)):
                return render_template('main.html', answer="❌ 当前视频模型仅支持在线图片 URL", **form_values)
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
            chat_ids = json.loads(request.form.get("chat_file_ids", "[]"))
            attachments = deepseek_state.references(session["deepseek_owner"], ui_session_id, deepseek_key, chat_ids)
            form_values["chat_attachments"] = attachments
            form_values["chat_file_ids"] = json.dumps(chat_ids)
            if attachments and not user_query:
                user_query = "请分析附件图片。"
                form_values["query"] = user_query
            llm = ChatDeepSeek(model="deepseek-flash", api_key=deepseek_key, chat_file_ids=chat_ids)
        except ValueError as error:
            detail = str(error) if isinstance(error, DeepSeekError) else "请检查密钥及附件参数"
            answer = f"❌ 初始化 DeepSeek LLM 失败: {detail}"
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

            run_exe_tool = StructuredTool.from_function(
                run_executable_tool,
                name="run_executable",
                description="在配置根目录内运行 .exe、.bat、.cmd 或无扩展名文件。参数 path 是相对或绝对路径。",
            )
            run_ps1_tool = StructuredTool.from_function(
                run_powershell_tool,
                name="run_powershell",
                description="在配置根目录内运行 .ps1 脚本。参数 path 是相对或绝对路径。",
            )
            cloud_tools, cloud_pending, cloud_events, web_sources = make_deepseek_tools(
                DeepSeekAPI(deepseek_key), deepseek_state, session["deepseek_owner"], ui_session_id, use_network)
            form_values["cloud_pending"] = cloud_pending
            system_prompt = "\n".join(part for part in (AGENT_RULES, system_prompt_input) if part)
            agent = create_agent(
                model=llm,
                tools=file_tools + [run_exe_tool, run_ps1_tool] + cloud_tools,
                system_prompt=system_prompt,
                middleware=[one_tool_per_step],
            )
        except Exception as e:
            answer = f"❌ 初始化文件工具失败: {e}"
            return render_template('main.html', answer=answer, **form_values)

        # Browser history stays local. Only the explicitly enabled memory and persona are sent.
        persona_context = "根据以下内容进行角色扮演：\n" + persona_text if persona_text else ""
        final_input = "\n\n".join(part for part in (memory_text, persona_context, user_query) if part)
        try:
            state = agent.invoke(
                {"messages": [{"role": "user", "content": final_input}]},
                config={"recursion_limit": 48, "max_concurrency": 1},
            )
            # A return_direct tool (the cloud deletion proposal) returns a ToolMessage.
            answer_text = state["messages"][-1].text
            if not answer_text:
                answer_text = "⚠️ DeepSeek 未返回可显示的文本。"

            summary = tool_summary(cloud_events, web_sources)
            if summary:
                answer_text += "\n\n" + summary
            deepseek_answer = answer_text
            form_values["deepseek_answer"] = deepseek_answer
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
        except GraphRecursionError:
            answer = "⚠️ 本次对话已达到工具调用上限，请缩小请求范围后重试。"
        except Exception:
            # Provider exceptions can contain request material. Keep them out of the response.
            answer = "❌ DeepSeek 对话请求失败，请检查密钥、网络及服务状态后重试。"

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
    import webbrowser
    from threading import Timer

    Timer(1, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False)

"""HelloCloud (https://router.hello.ai) 画像生成バックエンド。

HelloCloud の OpenAI 互換 ``/v1/images/generations`` 表面で
``krea-2-medium-turbo`` (Krea 2 Medium Turbo)、
``krea-2-turbo-uncensored`` (Krea 2 Turbo Uncensored)、
``minimax-h3-image`` (MiniMax H3 Image) を利用します。
出力は base64 (``b64_json``) で同期返却され、``$HERMES_HOME/cache/images/`` に保存されます。
MiniMax H3 Image では ``image_gen.hellocloud.output_format`` で PNG / JPEG、
``image_gen.hellocloud.resolution`` で 1K / 2K / 4K を選べます。

このプラグインは Hermes の公開プラグイン API (``agent.image_gen_provider``)
と ``openai`` SDK にのみ依存します。Hermes 内部の非公開モジュールには触れず、
バージョンアップに強くしています。

設定
----
``config.yaml``::

    image_gen:
      provider: hellocloud
      model: krea-2-medium-turbo        # 任意 (既定)
      hellocloud:
        base_url: https://api.router.hello.ai/v1   # 任意 (既定)
        resolution: 1K                              # モデルに応じて 1K / 2K / 4K
        output_format: png                         # MiniMax H3 は png / jpeg

認証キーは **環境変数 ``HELLOCLOUD_API_KEY``** から取得します (有料キー ``hello_...``。
トライアルキーは画像生成エンドポイントで使えません)。``.env`` への自動書き込み
は行わず、環境変数から注入する想定です。

ドキュメント: https://router.hello.ai/docs
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
from typing import Any, Dict, List, Optional, Tuple

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)
from agent.secret_scope import get_secret

logger = logging.getLogger(__name__)

# --- 定数 -------------------------------------------------------------------
DEFAULT_BASE_URL = "https://api.router.hello.ai/v1"
API_KEY_ENV = "HELLOCLOUD_API_KEY"
BASE_URL_ENV = "HELLOCLOUD_IMAGE_BASE_URL"
REQUEST_TIMEOUT_SECONDS = 1800.0  # 混雑時はキュー待ちが十数分になることがある

# HelloCloud 画像モデルカタログ (GET /v1/images/models 参照)。ID は /v1/images/generations
# にそのまま送られる。Hermes からは 1 リクエスト = 1 枚で生成する。
_MODELS: Dict[str, Dict[str, Any]] = {
    "krea-2-medium-turbo": {
        "display": "Krea 2 Medium Turbo (HelloCloud)",
        "speed": "~30-60s",
        "strengths": "1K PNG 生成、1 枚の参照画像 (image-to-image) / seed 対応。1K 1 枚 約 ¥3",
        "price": "1K 1 枚 約 ¥3 (4,175 image tokens @ ¥720/M)",
        "max_reference_images": 1,
        "output_formats": ("png",),
        "resolutions": ("1K",),
    },
    "krea-2-turbo-uncensored": {
        "display": "Krea 2 Turbo Uncensored (HelloCloud)",
        "speed": "1K / 2K",
        "strengths": "FP8 text-to-image / seed 対応。API 直接利用時は最大 4 枚、参照画像は非対応",
        "price": "1 枚 ¥10 (1K / 2K 同額)",
        "max_reference_images": 0,
        "output_formats": ("png",),
        "resolutions": ("1K", "2K"),
    },
    "minimax-h3-image": {
        "display": "MiniMax H3 Image (HelloCloud)",
        "speed": "1K / 2K: ~5s、4K: ~25s",
        "strengths": "FP8 text-to-image。1K / 2K / 4K、PNG / JPEG、9 種類の比率 / seed 対応",
        "price": "画像 ¥800/M tok + テキスト入力 ¥600/M tok (1K 1:1 は画像分 約 ¥0.8)",
        "max_reference_images": 0,
        "output_formats": ("png", "jpeg"),
        "resolutions": ("1K", "2K", "4K"),
    },
}
DEFAULT_MODEL = "krea-2-medium-turbo"

# Hermes のセマンティックアスペクト → HelloCloud/OpenAI の ``size``。
# HelloCloud は size (約分した比が対応表にあるもの) か aspect_ratio の両方を受理するが、
# OpenAI 標準の size を使う。3:2 / 1:1 / 2:3 はすべて HelloCloud 対応。
_SIZE_BY_ASPECT: Dict[str, str] = {
    "landscape": "1536x1024",   # 3:2
    "square": "1024x1024",      # 1:1
    "portrait": "1024x1536",    # 2:3
}
_ASPECT_BY_ASPECT: Dict[str, str] = {
    "landscape": "3:2",
    "square": "1:1",
    "portrait": "2:3",
}
_H3_SIZE_BY_RESOLUTION: Dict[str, Dict[str, str]] = {
    "1K": {"landscape": "1248x832", "square": "1024x1024", "portrait": "832x1248"},
    "2K": {"landscape": "2496x1664", "square": "2048x2048", "portrait": "1664x2496"},
    "4K": {"landscape": "5024x3360", "square": "4096x4096", "portrait": "3360x5024"},
}


def _size_for(aspect: str) -> str:
    return _SIZE_BY_ASPECT.get(aspect, _SIZE_BY_ASPECT["square"])


def _openai_importable() -> bool:
    try:
        import openai  # noqa: F401

        return True
    except ImportError:
        return False


def _resolve_api_key() -> Optional[str]:
    """``HELLOCLOUD_API_KEY`` を取得 (環境変数)。無ければ None。"""
    return get_secret(API_KEY_ENV)


def _resolve_base_url() -> str:
    """``image_gen.hellocloud.base_url`` → 環境変数 ``HELLOCLOUD_IMAGE_BASE_URL`` → 既定 URL。"""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else {}
        hellocloud = section.get("hellocloud") if isinstance(section, dict) else {}
        if isinstance(hellocloud, dict):
            val = hellocloud.get("base_url")
            if isinstance(val, str) and val.strip():
                return val.strip().rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    env = os.environ.get(BASE_URL_ENV, "").strip()
    return env or DEFAULT_BASE_URL


def _resolve_output_format(caller_format: Optional[str] = None) -> str:
    """``output_format`` 引数 → 環境変数 → ``image_gen.hellocloud.output_format`` → PNG。"""
    if isinstance(caller_format, str) and caller_format.strip():
        return caller_format.strip().lower()
    env = os.environ.get("HELLOCLOUD_IMAGE_OUTPUT_FORMAT", "").strip()
    if env:
        return env.lower()
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else {}
        hellocloud = section.get("hellocloud") if isinstance(section, dict) else {}
        if isinstance(hellocloud, dict):
            val = hellocloud.get("output_format")
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
    except Exception:  # noqa: BLE001
        pass
    return "png"


def _resolve_resolution(caller_resolution: Optional[str] = None) -> str:
    """``resolution`` 引数 → 環境変数 → ``image_gen.hellocloud.resolution`` → 1K。"""
    if isinstance(caller_resolution, str) and caller_resolution.strip():
        return caller_resolution.strip().upper()
    env = os.environ.get("HELLOCLOUD_IMAGE_RESOLUTION", "").strip()
    if env:
        return env.upper()
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else {}
        hellocloud = section.get("hellocloud") if isinstance(section, dict) else {}
        if isinstance(hellocloud, dict):
            val = hellocloud.get("resolution")
            if isinstance(val, str) and val.strip():
                return val.strip().upper()
    except Exception:  # noqa: BLE001
        pass
    return "1K"


def _resolve_model(caller_model: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """``model`` kwarg → 環境変数 ``HELLOCLOUD_IMAGE_MODEL`` → ``image_gen.hellocloud.model`` →
    ``image_gen.model`` (うちの ID のとき) → 既定。未知の ID は既定にフォールバック。"""
    candidates: List[Optional[str]] = []
    if isinstance(caller_model, str) and caller_model.strip():
        candidates.append(caller_model.strip())
    env = os.environ.get("HELLOCLOUD_IMAGE_MODEL", "").strip()
    if env:
        candidates.append(env)
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else {}
        hellocloud = section.get("hellocloud") if isinstance(section, dict) else {}
        if isinstance(hellocloud, dict):
            val = hellocloud.get("model")
            if isinstance(val, str) and val.strip():
                candidates.append(val.strip())
        if isinstance(section, dict):
            val = section.get("model")
            if isinstance(val, str) and val.strip():
                candidates.append(val.strip())
    except Exception:  # noqa: BLE001
        pass
    for candidate in candidates:
        if candidate in _MODELS:
            return candidate, _MODELS[candidate]
    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _to_reference_url(ref: str) -> Tuple[Optional[str], Optional[str]]:
    """参照画像を HelloCloud が受理する URL に変換 (https URL / data URL はそのまま、
    ローカルパスは data URL 化)。``(url, error)`` を返す。"""
    ref = (ref or "").strip()
    if not ref:
        return None, "参照画像が空です"
    if ref.startswith(("http://", "https://", "data:")):
        return ref, None
    try:
        with open(ref, "rb") as fh:
            data = fh.read()
    except Exception as exc:  # noqa: BLE001
        return None, f"参照画像 {ref} を読み込めませんでした: {exc}"
    ext = os.path.splitext(ref)[1].lstrip(".").lower()
    mime = (mimetypes.guess_type(f".{ext}")[0] if ext else None) or "image/png"
    if not mime.startswith("image/"):
        mime = "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}", None


class HelloCloudImageGenProvider(ImageGenProvider):
    """HelloCloud ``/v1/images/generations`` バックエンド (Krea 2 / MiniMax H3)。"""

    @property
    def name(self) -> str:
        # 設定キー (image_gen.provider: hellocloud) に一致する識別子。
        return "hellocloud"

    def is_available(self) -> bool:
        # 呼び出し時はキー + SDK のみ確認 (ネットワーク接続しない)。
        return bool(_resolve_api_key()) and _openai_importable()

    def capabilities(self) -> Dict[str, Any]:
        _model_id, meta = _resolve_model()
        max_refs = int(meta.get("max_reference_images", 1))
        modalities = ["text", "image"] if max_refs else ["text"]
        capabilities: Dict[str, Any] = {
            "modalities": modalities, "max_reference_images": max_refs,
        }
        if len(meta.get("resolutions", ("1K",))) > 1:
            capabilities["creative_controls"] = ["resolution"]
        return capabilities

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": meta["price"],
            }
            for model_id, meta in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        # キーは環境変数注入が前提なので、プロンプト付きの env_vars は出さない。
        return {
            "name": "HelloCloud",
            "badge": "paid",
            "tag": "Krea 2 / MiniMax H3 Image via HelloCloud (api.router.hello.ai) — HELLOCLOUD_API_KEY (環境変数)",
            "env_vars": [],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        model_id, _meta = _resolve_model(kwargs.get("model"))
        output_format = _resolve_output_format(kwargs.get("output_format"))
        resolution = _resolve_resolution(kwargs.get("resolution"))

        if not prompt:
            return error_response(
                error="プロンプトは必須で、空ではない文字列である必要があります。",
                error_type="invalid_argument", provider="hellocloud", model=model_id,
                aspect_ratio=aspect,
            )
        allowed_formats = tuple(_meta.get("output_formats", ("png",)))
        if output_format not in allowed_formats:
            return error_response(
                error=f"{model_id} の output_format は {' / '.join(allowed_formats)} のみ対応しています "
                      f"(指定: {output_format})。",
                error_type="invalid_argument", provider="hellocloud", model=model_id,
                prompt=prompt, aspect_ratio=aspect,
            )
        allowed_resolutions = tuple(_meta.get("resolutions", ("1K",)))
        if resolution not in allowed_resolutions:
            return error_response(
                error=f"{model_id} の resolution は {' / '.join(allowed_resolutions)} のみ対応しています "
                      f"(指定: {resolution})。",
                error_type="invalid_argument", provider="hellocloud", model=model_id,
                prompt=prompt, aspect_ratio=aspect,
            )

        api_key = _resolve_api_key()
        if not api_key:
            return error_response(
                error=f"{API_KEY_ENV} が設定されていません。環境変数 HELLOCLOUD_API_KEY に "
                      f"有料キー (hello_...) を設定してください (https://router.hello.ai)。",
                error_type="auth_required", provider="hellocloud", model=model_id,
                prompt=prompt, aspect_ratio=aspect,
            )

        try:
            import openai
        except ImportError:
            return error_response(
                error="openai パッケージが必要です (pip install openai)。",
                error_type="missing_dependency", provider="hellocloud", model=model_id,
                prompt=prompt, aspect_ratio=aspect,
            )

        # 参照画像: 主 ``image_url`` を最優先、次は reference_image_urls の先頭。
        # 対応モデルでは 1 枚まで。ローカルパス → data URL。
        ref_sources: List[str] = []
        if isinstance(image_url, str) and image_url.strip():
            ref_sources.append(image_url.strip())
        if reference_image_urls:
            for ref in reference_image_urls:
                if isinstance(ref, str) and ref.strip():
                    ref_sources.append(ref.strip())
        if ref_sources and not _meta.get("max_reference_images", 1):
            return error_response(
                error=f"{model_id} は参照画像 (image-to-image) に対応していません。",
                error_type="invalid_argument", provider="hellocloud", model=model_id,
                prompt=prompt, aspect_ratio=aspect,
            )
        if len(ref_sources) > 1:
            ref_sources = ref_sources[:1]

        request: Dict[str, Any] = dict(model=model_id, prompt=prompt, n=1)
        if model_id == "minimax-h3-image":
            request["output_format"] = output_format
            request["extra_body"] = {
                "aspect_ratio": _ASPECT_BY_ASPECT[aspect], "resolution": resolution,
            }
        else:
            request["size"] = _size_for(aspect)
            if model_id == "krea-2-turbo-uncensored" and resolution != "1K":
                request["extra_body"] = {"resolution": resolution}
        if ref_sources:
            url, rerr = _to_reference_url(ref_sources[0])
            if rerr:
                return error_response(
                    error=f"参照画像の処理に失敗しました: {rerr}",
                    error_type="invalid_argument", provider="hellocloud", model=model_id,
                    prompt=prompt, aspect_ratio=aspect,
                )
            # HelloCloud 固有フィールド ``input_references`` を JSON body のトップレベルに
            # 足す (OpenAI SDK の extra_body がマージする)。
            request["extra_body"] = {
                "input_references": [
                    {"type": "image_url", "image_url": {"url": url}},
                ]
            }

        client = openai.OpenAI(
            api_key=api_key, base_url=_resolve_base_url(), timeout=REQUEST_TIMEOUT_SECONDS,
        )
        try:
            response = client.images.generate(**request)
        except Exception as exc:  # noqa: BLE001
            logger.debug("HelloCloud image generation failed", exc_info=True)
            status = int(getattr(exc, "status_code", 0) or 0)
            detail = str(getattr(exc, "message", "") or "")
            body = getattr(exc, "body", None)
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict) and err.get("message"):
                    detail = str(err["message"])
                elif isinstance(err, str):
                    detail = err
            suffix = f" (HTTP {status})" if status else ""
            detail = f": {detail}" if detail else ""
            if status in (401, 403):
                return error_response(
                    error=f"HelloCloud が API キーを拒否しました{suffix}{detail}。"
                          f"トライアルキーは画像生成で使えません。有料キー (hello_...) を "
                          f"HELLOCLOUD_API_KEY に設定してください。",
                    error_type="auth_required", provider="hellocloud", model=model_id,
                    prompt=prompt, aspect_ratio=aspect,
                )
            return error_response(
                error=f"HelloCloud 画像生成に失敗しました{suffix}{detail}",
                error_type="api_error", provider="hellocloud", model=model_id,
                prompt=prompt, aspect_ratio=aspect,
            )

        data = getattr(response, "data", None) or []
        if not data:
            return error_response(
                error="HelloCloud のレスポンスに画像データがありません。",
                error_type="empty_response", provider="hellocloud", model=model_id,
                prompt=prompt, aspect_ratio=aspect,
            )
        first = data[0]
        b64 = getattr(first, "b64_json", None)
        url = getattr(first, "url", None)
        if not b64 and not url:
            return error_response(
                error="HelloCloud のレスポンスに b64_json も URL もありません。",
                error_type="empty_response", provider="hellocloud", model=model_id,
                prompt=prompt, aspect_ratio=aspect,
            )

        image_ref: Optional[str] = None
        try:
            if b64:
                extension = "jpg" if model_id == "minimax-h3-image" and output_format == "jpeg" else "png"
                image_ref = str(save_b64_image(b64, prefix="hellocloud", extension=extension))
            elif url:
                image_ref = str(save_url_image(url, prefix="hellocloud"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("HelloCloud image cache save failed: %s", exc)
            if url:
                # キャッシュ失敗時は URL をそのまま返す (best effort)。
                image_ref = url
            else:
                return error_response(
                    error=f"画像のキャッシュ保存に失敗しました: {exc}",
                    error_type="io_error", provider="hellocloud", model=model_id,
                    prompt=prompt, aspect_ratio=aspect,
                )
        if not image_ref:
            return error_response(
                error="画像の保存に失敗しました。",
                error_type="io_error", provider="hellocloud", model=model_id,
                prompt=prompt, aspect_ratio=aspect,
            )

        if model_id == "minimax-h3-image":
            extra: Dict[str, Any] = {"size": _H3_SIZE_BY_RESOLUTION[resolution][aspect]}
        elif model_id == "krea-2-turbo-uncensored":
            # API の実出力寸法は size 引数と一致しないため、推定値を返さない。
            extra = {}
        else:
            extra = {"size": _size_for(aspect)}
        if model_id == "minimax-h3-image":
            extra["output_format"] = output_format
            extra["resolution"] = resolution
        elif model_id == "krea-2-turbo-uncensored":
            extra["resolution"] = resolution
        usage = getattr(response, "usage", None)
        if usage is not None and getattr(usage, "completion_tokens", None):
            try:
                extra["image_tokens"] = int(usage.completion_tokens)
            except Exception:  # noqa: BLE001
                pass
        if getattr(first, "revised_prompt", None):
            extra["revised_prompt"] = first.revised_prompt

        return success_response(
            image=image_ref, model=model_id, prompt=prompt, aspect_ratio=aspect,
            provider="hellocloud", modality="image" if ref_sources else "text", extra=extra,
        )


def register(ctx) -> None:
    """プラグインエントリポイント — HelloCloud バックエンドを image_gen レジストリに登録。"""
    ctx.register_image_gen_provider(HelloCloudImageGenProvider())

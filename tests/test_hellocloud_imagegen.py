import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import __init__ as plugin


class HelloCloudImageGenProviderTest(unittest.TestCase):
    @patch.object(plugin, "save_b64_image", return_value="/tmp/hellocloud-test.png")
    @patch("openai.OpenAI")
    @patch.object(plugin, "_resolve_api_key", return_value="hello_test")
    def test_uncensored_model_generates_with_requested_model(
        self, _resolve_api_key, openai_class, _save_b64_image
    ):
        client = openai_class.return_value
        client.images.generate.return_value = SimpleNamespace(
            data=[SimpleNamespace(
                b64_json=base64.b64encode(b"png").decode("ascii"),
                url=None,
                revised_prompt=None,
            )],
            usage=SimpleNamespace(images=1),
        )

        result = plugin.HelloCloudImageGenProvider().generate(
            "A lighthouse at dusk", model="krea-2-turbo-uncensored"
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["model"], "krea-2-turbo-uncensored")
        request = client.images.generate.call_args.kwargs
        self.assertEqual(request["model"], "krea-2-turbo-uncensored")
        self.assertEqual(request["n"], 1)
        self.assertNotIn("extra_body", request)

    @patch.object(plugin, "save_b64_image", return_value="/tmp/hellocloud-test.jpg")
    @patch("openai.OpenAI")
    @patch.object(plugin, "_resolve_api_key", return_value="hello_test")
    def test_minimax_h3_generates_jpeg_with_requested_format(
        self, _resolve_api_key, openai_class, save_b64_image
    ):
        client = openai_class.return_value
        client.images.generate.return_value = SimpleNamespace(
            data=[SimpleNamespace(
                b64_json=base64.b64encode(b"jpeg").decode("ascii"),
                url=None,
                revised_prompt=None,
                media_type="image/jpeg",
            )],
            usage=SimpleNamespace(completion_tokens=1024, prompt_tokens=12),
        )

        result = plugin.HelloCloudImageGenProvider().generate(
            "A lighthouse at dusk", model="minimax-h3-image", output_format="jpeg"
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["model"], "minimax-h3-image")
        self.assertEqual(result["output_format"], "jpeg")
        request = client.images.generate.call_args.kwargs
        self.assertEqual(request["model"], "minimax-h3-image")
        self.assertEqual(request["output_format"], "jpeg")
        self.assertEqual(request["n"], 1)
        self.assertEqual(save_b64_image.call_args.kwargs["extension"], "jpg")

    @patch.object(plugin, "save_b64_image", return_value="/tmp/hellocloud-test.jpg")
    @patch("openai.OpenAI")
    @patch.object(plugin, "_resolve_api_key", return_value="hello_test")
    def test_minimax_h3_generates_4k_with_requested_resolution(
        self, _resolve_api_key, openai_class, _save_b64_image
    ):
        client = openai_class.return_value
        client.images.generate.return_value = SimpleNamespace(
            data=[SimpleNamespace(
                b64_json=base64.b64encode(b"jpeg").decode("ascii"),
                url=None,
                revised_prompt=None,
                media_type="image/jpeg",
            )],
            usage=SimpleNamespace(completion_tokens=16384, prompt_tokens=12),
        )

        result = plugin.HelloCloudImageGenProvider().generate(
            "A lighthouse at dusk",
            aspect_ratio="square",
            model="minimax-h3-image",
            resolution="4K",
            output_format="jpeg",
        )

        self.assertTrue(result["success"])
        self.assertEqual(result.get("resolution"), "4K")
        self.assertEqual(result.get("size"), "4096x4096")
        request = client.images.generate.call_args.kwargs
        self.assertNotIn("size", request)
        self.assertEqual(
            request["extra_body"], {"aspect_ratio": "1:1", "resolution": "4K"}
        )

    @patch.object(plugin, "save_b64_image", return_value="/tmp/hellocloud-test.jpg")
    @patch("openai.OpenAI")
    @patch.object(plugin, "_resolve_api_key", return_value="hello_test")
    def test_minimax_h3_reads_model_and_format_from_config(
        self, _resolve_api_key, openai_class, _save_b64_image
    ):
        client = openai_class.return_value
        client.images.generate.return_value = SimpleNamespace(
            data=[SimpleNamespace(
                b64_json=base64.b64encode(b"jpeg").decode("ascii"),
                url=None,
                revised_prompt=None,
                media_type="image/jpeg",
            )],
            usage=SimpleNamespace(completion_tokens=1024, prompt_tokens=12),
        )
        config = {
            "image_gen": {
                "model": "minimax-h3-image",
                "hellocloud": {"output_format": "jpeg"},
            }
        }

        with patch("hermes_cli.config.load_config", return_value=config):
            result = plugin.HelloCloudImageGenProvider().generate("A lighthouse at dusk")

        self.assertTrue(result["success"])
        self.assertEqual(result["model"], "minimax-h3-image")
        self.assertEqual(result["output_format"], "jpeg")
        request = client.images.generate.call_args.kwargs
        self.assertEqual(request["model"], "minimax-h3-image")
        self.assertEqual(request["output_format"], "jpeg")

    @patch.object(plugin, "save_b64_image", return_value="/tmp/hellocloud-test.png")
    @patch("openai.OpenAI")
    @patch.object(plugin, "_resolve_api_key", return_value="hello_test")
    def test_uncensored_reads_2k_resolution_from_config(
        self, _resolve_api_key, openai_class, _save_b64_image
    ):
        client = openai_class.return_value
        client.images.generate.return_value = SimpleNamespace(
            data=[SimpleNamespace(
                b64_json=base64.b64encode(b"png").decode("ascii"),
                url=None,
                revised_prompt=None,
            )],
            usage=SimpleNamespace(images=1),
        )
        config = {
            "image_gen": {
                "model": "krea-2-turbo-uncensored",
                "hellocloud": {"resolution": "2K"},
            }
        }

        with patch("hermes_cli.config.load_config", return_value=config):
            result = plugin.HelloCloudImageGenProvider().generate("A lighthouse at dusk")

        self.assertTrue(result["success"])
        self.assertEqual(result.get("resolution"), "2K")
        self.assertNotIn("size", result)
        request = client.images.generate.call_args.kwargs
        self.assertEqual(request["model"], "krea-2-turbo-uncensored")
        self.assertEqual(request["extra_body"], {"resolution": "2K"})

    @patch.object(plugin, "save_b64_image", return_value="/tmp/hellocloud-test.png")
    @patch("openai.OpenAI")
    @patch.object(plugin, "_resolve_api_key", return_value="hello_test")
    def test_hermes_dispatch_forwards_resolution_control(
        self, _resolve_api_key, openai_class, _save_b64_image
    ):
        from tools import image_generation_tool as core

        client = openai_class.return_value
        client.images.generate.return_value = SimpleNamespace(
            data=[SimpleNamespace(
                b64_json=base64.b64encode(b"png").decode("ascii"),
                url=None,
                revised_prompt=None,
                media_type="image/png",
            )],
            usage=SimpleNamespace(completion_tokens=1024, prompt_tokens=12),
        )
        provider = plugin.HelloCloudImageGenProvider()

        with (
            patch.object(core, "_read_configured_image_provider", return_value="hellocloud"),
            patch.object(core, "_read_configured_image_model", return_value="minimax-h3-image"),
            patch.object(core, "_get_plugin_provider", return_value=provider),
            patch("hermes_cli.config.load_config", return_value={
                "image_gen": {"model": "minimax-h3-image"}
            }),
        ):
            raw = core._dispatch_to_plugin_provider(
                "A lighthouse at dusk", "square", controls={"resolution": "2K"}
            )

        self.assertTrue(json.loads(raw or "")["success"])
        request = client.images.generate.call_args.kwargs
        self.assertEqual(
            request["extra_body"], {"aspect_ratio": "1:1", "resolution": "2K"}
        )

    @patch("openai.OpenAI")
    @patch.object(plugin, "_resolve_api_key", return_value="hello_test")
    def test_minimax_h3_rejects_unknown_output_format(self, _resolve_api_key, openai_class):
        result = plugin.HelloCloudImageGenProvider().generate(
            "A lighthouse at dusk", model="minimax-h3-image", output_format="webp"
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "invalid_argument")
        self.assertIn("png", result["error"])
        self.assertIn("jpeg", result["error"])
        openai_class.return_value.images.generate.assert_not_called()

    @patch("openai.OpenAI")
    @patch.object(plugin, "_resolve_api_key", return_value="hello_test")
    def test_krea_medium_rejects_unsupported_2k_resolution(self, _resolve_api_key, openai_class):
        result = plugin.HelloCloudImageGenProvider().generate(
            "A lighthouse at dusk", model="krea-2-medium-turbo", resolution="2K"
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "invalid_argument")
        self.assertIn("1K", result["error"])
        openai_class.return_value.images.generate.assert_not_called()

    @patch("openai.OpenAI")
    @patch.object(plugin, "_resolve_api_key", return_value="hello_test")
    def test_uncensored_model_rejects_reference_images(self, _resolve_api_key, openai_class):
        result = plugin.HelloCloudImageGenProvider().generate(
            "A lighthouse at dusk",
            model="krea-2-turbo-uncensored",
            image_url="https://example.com/reference.png",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "invalid_argument")
        self.assertIn("参照画像", result["error"])
        openai_class.return_value.images.generate.assert_not_called()

    def test_capabilities_follow_selected_model(self):
        with patch.object(
            plugin,
            "_resolve_model",
            return_value=(
                "krea-2-turbo-uncensored",
                plugin._MODELS["krea-2-turbo-uncensored"],
            ),
        ):
            capabilities = plugin.HelloCloudImageGenProvider().capabilities()

        self.assertEqual(capabilities["modalities"], ["text"])
        self.assertEqual(capabilities["max_reference_images"], 0)


if __name__ == "__main__":
    unittest.main()

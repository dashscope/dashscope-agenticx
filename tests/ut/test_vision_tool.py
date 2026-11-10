# -*- coding: utf-8 -*-
# Copyright (c) Alibaba, Inc. and its affiliates.
# pylint: disable=protected-access

from types import SimpleNamespace

import pytest

from dashscope_agenticx.tools import vision
from dashscope_agenticx.tools.registry import registry


@pytest.mark.parametrize("version", ["12.3.0", "invalid", "0"])
def test_unsafe_pillow_decoder_is_rejected(version, monkeypatch):
    import sys

    monkeypatch.setitem(
        sys.modules,
        "PIL",
        SimpleNamespace(__version__=version),
    )
    with pytest.raises(ImportError, match="Pillow>=13.0.0"):
        vision._safe_pillow_image()


class FakeProvider:
    def __init__(self):
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        return SimpleNamespace(content="transcribed table text")


class CountingProvider:
    def __init__(self):
        self.calls = 0
        self.questions = []
        self.messages = None

    async def chat(self, messages):
        self.calls += 1
        self.messages = messages
        self.questions.append(messages[0]["content"][0]["text"])
        return SimpleNamespace(content=f"slice text {self.calls}")


def _config():
    return SimpleNamespace(
        provider="tongyi",
        model="qwen3.8-max",
        api_key="sk-test",
        base_url="",
    )


def _register(fake_provider):
    captured = {}

    def get_provider_fn(provider, model, api_key, base_url=None):
        captured.update(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
        return fake_provider

    vision.register_vision_tools(
        _config(),
        get_provider_fn=get_provider_fn,
    )
    return captured


class TestViewImage:
    async def test_sends_image_to_vision_model(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            vision,
            "is_vision_model",
            lambda model: False,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: SimpleNamespace(vision_models=["qwen-vl-plus"]),
        )
        fake_provider = FakeProvider()
        captured = _register(fake_provider)

        image = tmp_path / "shot.png"
        image.write_bytes(b"\x89PNG fake bytes")

        result = await registry.get("view_image").func(
            image_path=str(image),
        )

        assert result == "transcribed table text"
        assert captured["model"] == "qwen-vl-plus"
        content = fake_provider.messages[0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["image_url"]["url"].startswith(
            "data:image/png;base64,",
        )

    async def test_missing_file_returns_error(self):
        _register(FakeProvider())
        result = await registry.get("view_image").func(
            image_path=str("no-such-image.png"),
        )
        assert result.startswith("Error: failed to read image")

    async def test_vision_call_failure_returns_error(self, tmp_path):
        class BrokenProvider:
            async def chat(self, messages):
                raise RuntimeError("boom")

        _register(BrokenProvider())
        image = tmp_path / "shot.png"
        image.write_bytes(b"\x89PNG fake bytes")
        result = await registry.get("view_image").func(
            image_path=str(image),
        )
        assert result == "Error: vision call failed: boom"

    async def test_hung_vision_call_times_out(self, tmp_path, monkeypatch):
        # gcode-to-text (071n): a view_image await with no cap froze
        # the turn 700+s and the harness watchdog reaped the run.
        import asyncio

        class HungProvider:
            async def chat(self, messages):
                await asyncio.sleep(3600)

        monkeypatch.setattr(vision, "_VISION_CALL_TIMEOUT", 0.05)
        _register(HungProvider())
        image = tmp_path / "shot.png"
        image.write_bytes(b"\x89PNG fake bytes")
        result = await registry.get("view_image").func(
            image_path=str(image),
        )
        assert result.startswith("Error: vision call timed out after")
        assert "narrow the region" in result

    async def test_tall_image_auto_sliced(self, tmp_path):
        PIL = pytest.importorskip("PIL")
        provider = CountingProvider()
        _register(provider)

        img = PIL.Image.new("RGB", (200, 10000), "white")
        path = tmp_path / "tall.png"
        img.save(path)

        result = await registry.get("view_image").func(
            image_path=str(path),
        )

        expected = len(vision.plan_slices(200, 10000))
        assert provider.calls == expected
        assert all(q.startswith("Slice ") for q in provider.questions)
        assert result.startswith("(Image auto-split into")
        assert f"--- slice 1/{expected} ---" in result
        assert f"--- slice {expected}/{expected} ---" in result

    async def test_slice_never_sends_whole_image(self, tmp_path):
        PIL = pytest.importorskip("PIL")
        provider = CountingProvider()
        _register(provider)

        img = PIL.Image.new("RGB", (200, 10000), "white")
        path = tmp_path / "tall.png"
        img.save(path)

        result = await registry.get("view_image").func(
            image_path=str(path),
            slice_mode="never",
        )

        assert provider.calls == 1
        assert result == "slice text 1"

    async def test_slice_error_reported_per_slice(self, tmp_path):
        PIL = pytest.importorskip("PIL")

        class BrokenProvider:
            async def chat(self, messages):
                raise RuntimeError("boom")

        _register(BrokenProvider())
        img = PIL.Image.new("RGB", (200, 10000), "white")
        path = tmp_path / "tall.png"
        img.save(path)

        result = await registry.get("view_image").func(
            image_path=str(path),
        )
        assert "Error: boom" in result
        assert "--- slice 1/" in result


class TestPlanSlices:
    def test_normal_image_returns_none(self):
        assert vision.plan_slices(1920, 1080) is None
        assert vision.plan_slices(8000, 2000) is None

    def test_tall_image_slices_vertically(self):
        boxes = vision.plan_slices(1382, 12028)
        assert boxes is not None
        assert all(b[0] == 0 and b[2] == 1382 for b in boxes)
        assert boxes[0][1] == 0
        assert boxes[-1][3] == 12028
        for prev, nxt in zip(boxes, boxes[1:]):
            assert nxt[1] < prev[3]  # overlap
            assert nxt[1] == prev[3] - vision._SLICE_OVERLAP

    def test_wide_image_slices_horizontally(self):
        boxes = vision.plan_slices(20000, 800)
        assert boxes is not None
        assert all(b[1] == 0 and b[3] == 800 for b in boxes)
        assert boxes[-1][2] == 20000

    def test_extreme_image_capped(self):
        boxes = vision.plan_slices(1000, 1000000)
        assert len(boxes) <= vision._SLICE_MAX_COUNT
        assert boxes[-1][3] == 1000000

    def test_high_ratio_triggers_even_when_short(self):
        assert vision.plan_slices(1000, 4500) is not None
        assert vision.plan_slices(4500, 1000) is not None


class TestParseRegion:
    def test_fractions_to_pixels(self):
        assert vision.parse_region("0.25,0.5,0.75,1", 200, 100) == (
            50,
            50,
            150,
            100,
        )

    def test_minimum_one_pixel(self):
        box = vision.parse_region("0,0,0.001,0.001", 10, 10)
        assert box[2] > box[0] and box[3] > box[1]

    @pytest.mark.parametrize(
        "region",
        [
            "0,0,1",  # wrong count
            "0,0,1,1,1",
            "a,b,c,d",  # non-numeric
            "0.5,0,0.4,1",  # x0 >= x1
            "0,0.9,1,0.1",  # y0 >= y1
            "-0.1,0,1,1",  # below range
            "0,0,1,1.1",  # above range
        ],
    )
    def test_rejects_malformed(self, region):
        with pytest.raises(ValueError):
            vision.parse_region(region, 200, 100)


class TestUpscaleForVision:
    def test_small_crop_upscaled_to_short_side(self):
        PIL = pytest.importorskip("PIL")
        crop = PIL.Image.new("RGB", (500, 400), "white")
        out = vision._upscale_for_vision(crop)
        assert out.size == (960, 768)

    def test_upscale_factor_capped(self):
        PIL = pytest.importorskip("PIL")
        crop = PIL.Image.new("RGB", (100, 50), "white")
        out = vision._upscale_for_vision(crop)
        assert out.size == (400, 200)  # 4x cap, not 15.36x

    def test_large_crop_untouched(self):
        PIL = pytest.importorskip("PIL")
        crop = PIL.Image.new("RGB", (800, 900), "white")
        out = vision._upscale_for_vision(crop)
        assert out.size == (800, 900)


class TestRegionRead:
    async def test_region_sends_cropped_upscaled_image(
        self,
        tmp_path,
        monkeypatch,
    ):
        PIL = pytest.importorskip("PIL")
        monkeypatch.setattr(vision, "_REGION_SAMPLES", 1)
        provider = CountingProvider()
        _register(provider)

        img = PIL.Image.new("RGB", (200, 100), "white")
        path = tmp_path / "grid.png"
        img.save(path)

        result = await registry.get("view_image").func(
            image_path=str(path),
            region="0,0,0.5,0.5",
        )

        assert provider.calls == 1
        assert result == "slice text 1"
        url = provider.messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        import base64 as b64
        import io

        sent = PIL.Image.open(
            io.BytesIO(b64.b64decode(url.split(",", 1)[1])),
        )
        # crop 100x50, upscaled 4x (cap) → 400x200
        assert sent.size == (400, 200)

    async def test_region_overrides_slicing(self, tmp_path, monkeypatch):
        PIL = pytest.importorskip("PIL")
        monkeypatch.setattr(vision, "_REGION_SAMPLES", 1)
        provider = CountingProvider()
        _register(provider)

        img = PIL.Image.new("RGB", (200, 10000), "white")
        path = tmp_path / "tall.png"
        img.save(path)

        result = await registry.get("view_image").func(
            image_path=str(path),
            region="0,0,1,0.1",
        )

        assert provider.calls == 1
        assert not result.startswith("(Image auto-split")

    async def test_invalid_region_returns_error(self, tmp_path):
        PIL = pytest.importorskip("PIL")
        _register(FakeProvider())
        img = PIL.Image.new("RGB", (200, 100), "white")
        path = tmp_path / "grid.png"
        img.save(path)

        result = await registry.get("view_image").func(
            image_path=str(path),
            region="banana",
        )
        assert result.startswith("Error: invalid region")

    async def test_region_without_pillow_falls_back_to_whole_read(
        self,
        tmp_path,
        monkeypatch,
    ):
        # gcode-to-text (071o): Pillow is optional and the offline
        # bench bundle is pure-python — three region calls errored in
        # a row. The read must degrade, not die.
        import sys

        monkeypatch.setitem(sys.modules, "PIL", None)
        _register(FakeProvider())
        image = tmp_path / "shot.png"
        image.write_bytes(b"\x89PNG fake bytes")

        result = await registry.get("view_image").func(
            image_path=str(image),
            region="0,0,0.5,0.5",
        )
        assert "transcribed table text" in result
        assert "Pillow is not installed" in result
        assert "pillow>=13.0.0" in result
        assert not result.startswith("Error: failed to crop region")
        # 071u mistook the fallback for a region read when the warning
        # trailed the content; it must lead, and must say the read is no
        # independent verification.
        assert result.index("Pillow is not installed") < result.index(
            "transcribed table text",
        )
        assert "not independent verification" in result

    def test_region_in_schema(self):
        _register(FakeProvider())
        schema = registry.get("view_image").parameters
        assert "region" in schema["properties"]
        assert "x0,y0,x1,y1" in schema["properties"]["region"]["description"]

    def test_description_demands_region_verification_for_exact_text(self):
        # gcode-to-text (071m): with no consistency oracle, the model
        # committed the first plausible whole-image read of stylized
        # text and exited early — three runs, three wrong variants.
        # Exact-character work must be told to verify with regions.
        _register(FakeProvider())
        desc = registry.get("view_image").description
        assert "every character matters" in desc
        assert "never trust" in desc
        assert "region reads of a few characters" in desc

    def test_region_description_covers_exact_character_work(self):
        # 071m: the model never considered region for stylized text —
        # the old wording only named grids/tables.
        _register(FakeProvider())
        rdesc = registry.get("view_image").parameters["properties"]["region"][
            "description"
        ]
        assert "exact-character" in rdesc
        assert "few characters per call" in rdesc

    def test_description_demands_overlapping_crops(self):
        # gcode-to-text (071p): the model did verify with per-few-character
        # region reads but tiled them non-overlapping; the leading "g" sat
        # on a crop boundary, appeared in no crop's interior, and was
        # silently dropped (flag{c0d3... vs flag{gc0d3...). Same rationale
        # as _SLICE_OVERLAP in auto-slicing.
        _register(FakeProvider())
        desc = registry.get("view_image").description
        assert "overlap adjacent crops" in desc
        assert "crop boundary" in desc

    def test_region_description_demands_overlap_when_tiling(self):
        # 071p: boundary characters are the ones lost/duplicated; every
        # character must appear fully inside at least one crop.
        _register(FakeProvider())
        rdesc = registry.get("view_image").parameters["properties"]["region"][
            "description"
        ]
        assert "overlap adjacent crops" in rdesc
        assert "fully inside" in rdesc

    def test_description_demands_noise_floor_adjudication(self):
        # gcode-to-text (071r): identical re-reads of one stylized glyph
        # returned contradictory answers (i, ï, 1) and the model coin-flipped
        # to the wrong one. Once reads disagree, the glyph is at the
        # recognizer's noise floor — the description must redirect to a
        # feature question on a single-glyph crop plus a coherence check on
        # the assembled string.
        _register(FakeProvider())
        desc = registry.get("view_image").description
        assert "noise floor" in desc
        assert "feature" in desc
        assert "coherent text" in desc

    def test_description_demands_overlap_cross_check(self):
        # gcode-to-text (071t): two crops sharing a span read it
        # differently ("flagco" vs "0d3_iz") and the model merged the
        # contradiction by guessing. Overlap is also a consistency check,
        # not just completeness — shared characters must agree.
        _register(FakeProvider())
        desc = registry.get("view_image").description
        assert "cross-check" in desc
        assert "must read the same" in desc

    def test_description_demands_rerender_over_reread(self):
        # gcode-to-text (071t): three unanimous votes were still wrong —
        # the render itself was too coarse, and re-reading the same pixels
        # cannot fix that. Self-rendered images must be re-rendered bigger
        # or higher-contrast when reads come back ambiguous.
        _register(FakeProvider())
        desc = registry.get("view_image").description
        assert "re-render" in desc
        assert "re-reading the same pixels" in desc

    def test_description_demands_feature_check_for_confusable_glyphs(self):
        # gcode-to-text (071u): every verification targeted the glyphs the
        # model already doubted; the i→1 error survived because the
        # whole-read asserted it confidently and no feature question ever
        # isolated it. Confusable glyph sets need feature-level checks
        # precisely because stylized wrong glyphs look unambiguous.
        _register(FakeProvider())
        desc = registry.get("view_image").description
        assert "confusable" in desc
        assert "1/l/i/I" in desc
        assert "feature-level confirmation" in desc

    def test_description_demands_isolated_glyph_adjudication(self):
        # gcode-to-text (071v): word-level crops kept coming back as the
        # expected sentence ("cod3" over the attested "gc0d3") even as
        # explicit feature answers — language priors pattern-complete
        # word-shaped crops. Only lone-glyph reads are prior-free, so
        # contested characters must be settled glyph by glyph.
        _register(FakeProvider())
        desc = registry.get("view_image").description
        assert "pattern-completed" in desc
        assert "isolated glyph" in desc
        assert "glyph by glyph" in desc


class TestRegionVote:
    async def test_region_read_samples_default_three_times(self, tmp_path):
        PIL = pytest.importorskip("PIL")
        provider = CountingProvider()
        _register(provider)
        img = PIL.Image.new("RGB", (200, 100), "white")
        path = tmp_path / "grid.png"
        img.save(path)

        await registry.get("view_image").func(
            image_path=str(path),
            region="0,0,0.5,0.5",
        )
        assert provider.calls == 3

    async def test_split_samples_show_every_answer_and_hint(self, tmp_path):
        # gcode-to-text (071s): single reads per region hid the
        # disagreements and the model committed a coin flip. CountingProvider
        # answers differ per call, so the vote must surface all of them.
        PIL = pytest.importorskip("PIL")
        provider = CountingProvider()
        _register(provider)
        img = PIL.Image.new("RGB", (200, 100), "white")
        path = tmp_path / "grid.png"
        img.save(path)

        result = await registry.get("view_image").func(
            image_path=str(path),
            region="0,0,0.5,0.5",
        )
        assert "[sample 1/3]" in result
        assert "[sample 3/3]" in result
        assert "slice text 1" in result
        assert "slice text 3" in result
        assert "disagree" in result
        assert "coherent text" in result

    async def test_unanimous_samples_return_single_answer(self, tmp_path):
        PIL = pytest.importorskip("PIL")
        provider = FakeProvider()
        _register(provider)
        img = PIL.Image.new("RGB", (200, 100), "white")
        path = tmp_path / "grid.png"
        img.save(path)

        result = await registry.get("view_image").func(
            image_path=str(path),
            region="0,0,0.5,0.5",
        )
        assert result == "transcribed table text"
        assert "[sample" not in result

    def test_region_description_mentions_sampling(self):
        # 071s: the vote is only useful if the model knows disagreements
        # will be shown rather than hidden.
        _register(FakeProvider())
        rdesc = registry.get("view_image").parameters["properties"]["region"][
            "description"
        ]
        assert "several times" in rdesc
        assert "disagree" in rdesc


class TestPickVisionModel:
    def test_active_model_when_vision_capable(self, monkeypatch):
        monkeypatch.setattr(vision, "is_vision_model", lambda m: True)
        assert vision.pick_vision_model(_config()) == "qwen3.8-max"

    def test_extension_vision_models(self, monkeypatch):
        monkeypatch.setattr(vision, "is_vision_model", lambda m: False)
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: SimpleNamespace(vision_models=["qwen-vl-max"]),
        )
        assert vision.pick_vision_model(_config()) == "qwen-vl-max"

    def test_fallback_per_provider(self, monkeypatch):
        monkeypatch.setattr(vision, "is_vision_model", lambda m: False)
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: None,
        )
        # qwen-vl-max is retired on the public DashScope endpoint (404);
        # the tongyi fallback must be a currently-served VL model.
        assert vision.pick_vision_model(_config()) == "qwen3-vl-plus"
        cfg = _config()
        cfg.provider = "openai"
        assert vision.pick_vision_model(cfg) == "gpt-4o"


class TestSchemaExposure:
    def test_view_image_always_offered(self):
        vision.register_vision_tools(
            _config(),
            get_provider_fn=lambda *a, **k: FakeProvider(),
        )
        names = [t["name"] for t in registry.to_schema_list()]
        assert "view_image" in names


@pytest.fixture(autouse=True)
def _clean_vision_state():
    """register_vision_tools stores a process-global perception context;
    without a reset it leaks into other test files (read_file would
    auto-perceive mid-test) and counters pollute the tip tests."""
    vision._PERCEPTION_CTX = None
    vision._reset_view_image_calls()
    vision._reset_auto_perceive()
    yield
    vision._PERCEPTION_CTX = None
    vision._reset_view_image_calls()
    vision._reset_auto_perceive()


class TestAutoPerceive:
    """read_file's injection hook: perception delivered, not advised.

    071i evidence: the redirect text was read at t=136s and ignored
    within 3s. auto_perceive runs the same pipeline inline instead;
    every guard (env, ctx, budget) and the failure fallback exists
    because its mutation must flip exactly one test here.
    """

    def _ctx(self):
        return _config(), lambda *a, **k: FakeProvider()

    def test_none_without_ctx(self):
        assert vision._PERCEPTION_CTX is None
        assert vision.auto_perceive("/app/x.png") is None

    def test_env_disable(self, monkeypatch):
        vision._PERCEPTION_CTX = self._ctx()
        monkeypatch.setenv("AGENTICX_AUTO_VISION", "0")
        assert vision.auto_perceive("/app/x.png") is None
        # Disabled means not even an attempt is spent.
        assert vision.auto_perceive_count() == 0

    def test_budget_exhausted(self):
        vision._PERCEPTION_CTX = self._ctx()
        vision._auto_perceive_attempts = vision._auto_perceive_budget()
        assert vision.auto_perceive("/app/x.png") is None

    def test_env_budget_override(self, monkeypatch):
        monkeypatch.setenv("AGENTICX_AUTO_VISION_MAX", "1")
        assert vision._auto_perceive_budget() == 1
        monkeypatch.setenv("AGENTICX_AUTO_VISION_MAX", "not-a-number")
        assert vision._auto_perceive_budget() == vision._AUTO_PERCEIVE_MAX

    def test_delivers_perception(self, monkeypatch):
        vision._PERCEPTION_CTX = self._ctx()

        async def fake_perceive(config, get_provider_fn, path, q, mode):
            return "a 9 by 9 digit grid, partially filled"

        monkeypatch.setattr(vision, "_perceive_image", fake_perceive)
        result = vision.auto_perceive("/app/sudoku.jpeg")
        assert "9 by 9 digit grid" in result
        assert "auto-perception" in result
        assert "tesseract" in result
        assert vision.auto_perceive_count() == 1

    def test_pipeline_raise_falls_back(self, monkeypatch):
        vision._PERCEPTION_CTX = self._ctx()

        async def boom(config, get_provider_fn, path, q, mode):
            raise RuntimeError("endpoint down")

        monkeypatch.setattr(vision, "_perceive_image", boom)
        assert vision.auto_perceive("/app/x.png") is None
        # The attempt is spent even on failure — a broken endpoint must
        # not be retried on every image read.
        assert vision.auto_perceive_count() == 1

    def test_same_image_not_perceived_twice(self, monkeypatch):
        # sudoku runs reference the same image in ~10 commands; without
        # dedup each reference burns a ~20s vision call until the budget
        # is gone. The content is already in context after the first
        # read — a repeat returns None (and does not spend an attempt).
        vision._PERCEPTION_CTX = self._ctx()

        async def fake_perceive(config, get_provider_fn, path, q, mode):
            return "a digit grid"

        monkeypatch.setattr(vision, "_perceive_image", fake_perceive)
        assert vision.auto_perceive("/app/x.png") is not None
        assert vision.auto_perceive("/app/x.png") is None
        assert vision.auto_perceive_count() == 1

    def test_command_via_prefix(self, monkeypatch):
        # run_command's inject frames the read differently from
        # read_file's (no "cannot decode as text" — the command reads
        # fine, it just cannot see).
        vision._PERCEPTION_CTX = self._ctx()

        async def fake_perceive(config, get_provider_fn, path, q, mode):
            return "a digit grid"

        monkeypatch.setattr(vision, "_perceive_image", fake_perceive)
        result = vision.auto_perceive("/app/x.png", via="command")
        assert "this command references an image" in result
        assert "a digit grid" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

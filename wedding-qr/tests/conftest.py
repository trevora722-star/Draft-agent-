import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wedding_qr import config, db  # noqa: E402


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"

    def fake_settings():
        return config.Settings(
            data_dir=data_dir,
            db_path=data_dir / "wedding_qr.db",
            photos_dir=data_dir / "photos",
            thumbnails_dir=data_dir / "thumbnails",
            base_url="http://testserver",
        )

    for mod_name in ("wedding_qr.config", "wedding_qr.db", "wedding_qr.storage", "wedding_qr.qr", "wedding_qr.llm"):
        if mod_name in sys.modules:
            monkeypatch.setattr(sys.modules[mod_name], "get_settings", fake_settings, raising=False)

    db.init_db(data_dir / "wedding_qr.db")
    return fake_settings()


@pytest.fixture
def sample_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (64, 64), color=(200, 150, 120))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

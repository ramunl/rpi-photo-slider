# Face-detection test fixtures

The real-model acceptance tests in `test_faces.py` run **real YuNet inference** —
they never mock detector outputs — so they need real photographs. The fixtures
are:

| File | Content | Expected | License |
| --- | --- | --- | --- |
| `single.jpg` | One forward-facing subject | exactly 1 face | Public domain (NASA) |
| `group.jpg` | Several forward-facing subjects | >= 2 faces | Public domain (NASA) |

Provenance is recorded in `manifest.json` (source URL, license, attribution, and
the expected face counts/regions). Populate the images with:

```sh
python3 tests/fixtures/faces/fetch_fixtures.py
```

The script downloads each image, records its SHA-256 in `manifest.json`, and
refuses to overwrite an existing file whose bytes differ. Fixture redistribution
rights are documented separately from the YuNet model license (Apache-2.0);
these images are United States government works in the public domain.

Until the images are present, the real-model fixture tests skip automatically;
the cache and detector unit tests still run.

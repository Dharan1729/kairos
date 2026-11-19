# Demo org generator

`generate_synthetic_org.py` writes the synthetic demo org that `kairos seed` and `kairos quickstart` load, into `kairos/demo_data/` (shipped inside the package):

```bash
python knowledge-layer/generate_synthetic_org.py
```

Pure standard library, seeded (`random.seed(42)`), so reruns produce identical files. See [SCHEMA.md](SCHEMA.md) for the shape of each file.

What is in it: one fictional org ("Nimbus Robotics"), 4 teams, 16 people with generated first names, 4 projects with repo names, 16 sessions, and 32 facts across all five layers (3 org, 4 team, 4 project, 16 personal, 5 session). Some facts are deliberate paraphrases of each other, and pairs of sessions share a task description, to exercise ranking and the cache.

The names are combinations of common first names picked at random; they do not refer to real people. No emails or other personal data.

The demo org is too small to measure selection quality: everything fits in any reasonable budget. The benchmarks use a separate, larger, labelled org in `benchmarks/dataset.py`.

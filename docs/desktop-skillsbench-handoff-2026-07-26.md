# Merlin desktop SkillsBench handoff — 2026-07-26

## Decision

Do not install or run Docker on the current Mac. It has only 30 GiB free.
Continue the benchmark preflight on the desktop with at least 80 GiB free;
120 GiB or more is recommended for repeated builds and retained evidence.

## Capacity estimate

- Current Merlin source tree: about 0.5 GiB including local build products;
  the publishable source snapshot is much smaller.
- Frozen SkillsBench repository: GitHub reports about 865 MiB of repository
  data. Budget 1–2 GiB for checkout and tooling metadata.
- Docker Desktop, VM storage, package caches, and three smoke-task images:
  budget 15–30 GiB.
- A sequential 87-task campaign can accumulate roughly 30–100+ GiB of images
  and build cache, depending on scientific dependencies.
- Keep another 10–20 GiB for run outputs, immutable evidence, and temporary
  verifier workspaces.

The image/cache range is an engineering budget, not a measured corpus total.
Measure it after the three-task smoke run before scheduling the wider pilot.

## Frozen inputs

- Repository: `https://github.com/benchflow-ai/skillsbench`
- Commit: `5433cf15c343f0da5fb942b80dc7dcb7c76506df`
- Merlin split manifest SHA-256:
  `2ee814636509b5bfca50c436d333ea8a95498b16c144a86a88876ecb0eb6e20c`
- Split: 35 adaptation / 30 held-out / 22 regression
- Existing task packages already contain their Dockerfiles. Do not author 87
  new Dockerfiles.

## Desktop preflight

Keep the upstream benchmark checkout outside the Merlin Git worktree:

```bash
mkdir -p ~/Documents/merlin-benchmarks
cd ~/Documents/merlin-benchmarks
git clone https://github.com/benchflow-ai/skillsbench.git skillsbench-5433cf15
cd skillsbench-5433cf15
git checkout --detach 5433cf15c343f0da5fb942b80dc7dcb7c76506df
git rev-parse HEAD
```

Install and start Docker Desktop, then verify the local sandbox:

```bash
docker version
docker run --rm hello-world
python3 --version
uv --version
uv tool install benchflow
uv sync --locked
```

Python must be 3.12 or newer. Do not enter provider keys until the oracle-only
path passes.

## First bounded run

Start with the three adaptation smoke candidates:

1. `earthquake-plate-calculation`
2. `3d-scan-calc`
3. `crystallographic-wyckoff-position-analysis`

For each task:

1. run `bench tasks check tasks/<task-id>`;
2. run the oracle with `--sandbox docker`;
3. record build time, image size, peak disk use, verifier result, and output
   hash;
4. stop on the first package/oracle failure and classify it before any model
   run;
5. prune only task-specific disposable images/caches after evidence is sealed.

Do not use broad destructive cleanup commands without first resolving the exact
Docker targets.

## Experiment order

Use three explicit arms:

- `C0`: no skill;
- `C1`: selected/provisioned skill with frozen library and frozen policy;
- `M`: Merlin-managed lifecycle and bounded policy evolution.

The primary comparison is `M - C1`, because it isolates harness management from
mere skill availability. Use identical task package, model, timeout, verifier,
and seed schedule for matched arms.

After the three-task smoke succeeds, run a bounded pilot before any full-87
claim. A reasonable first pilot is 8 tasks per split with three matched rounds
for adaptation and held-out, and two rounds for regression.

## Evidence gate

Promote an observation only when one chain binds:

`task -> selected skill ID -> exact SKILL.md body hash -> model request hash
-> provider execution trace -> verifier result`

Skill exposure or prompt inclusion alone is not invocation evidence. A missing
provider turn identifier or mismatched hash must fail closed and must not append
to the promotion ledger.

## Schedule to the 2026-08-24 paper deadline

- July 26–29: desktop Docker preflight, three-task oracle smoke, disk baseline
- July 30–August 7: adaptation pilot
- August 8–13: held-out and regression pilot
- August 14–17: analysis, failure taxonomy, bounded ablations
- August 18–21: paper writing and figures
- August 22–24: rerun and submission buffer

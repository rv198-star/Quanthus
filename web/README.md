# Quanthus strategy attribution workbench

This is a thin, read-only viewer for versioned artifacts produced by the Quanthus strategy
attribution kernel. The kernel owns calculations, classifications, evidence flags, and explanation
facts. The browser only validates the workspace envelope, selects records, translates registered
labels, and renders values already present in the artifact.

The bundled `public/data/attribution-demo.v1.json` is a synthetic fixture. It is not strategy
evidence and cannot satisfy the formal delivery gates in `config/p3-gates.v1.json`.

## Local use

```bash
npm install
npm run dev
```

Use the import action to open a `quanthus.strategy-attribution-workspace.v1` JSON file. Imported
files are marked unverified because digest and evidence verification remain kernel responsibilities.
The download action returns the original loaded JSON text.

## Checks

```bash
npm run lint
npm test
```

The Web surface intentionally contains no effect arithmetic, contribution ranking, causal upgrade,
or risk/opportunity aggregation.

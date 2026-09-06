# Data & Indicator Catalog production deployment blocker — 2026-09-07

| Field | Value |
| --- | --- |
| Work item | `P3-DICT-001-E` / parent `P3-DICT-001` |
| Repository implementation | PASS through `a15ae68daf5c254309bc7dcc73a88320cfd4c21c` |
| Production deployment | BLOCKED |
| Production claim | NOT ACCEPTED / NOT CLOSED |
| A-D child status | CLOSED by repository evidence |
| E / parent issue status | must remain OPEN until production recovery and smoke |

## 1. What passed before deployment

The Catalog repository implementation and its release packaging passed the focused and full gates:

- deterministic Catalog snapshot: 664 entries, 8 explicit gaps, digest
  `sha256:6f3d1a4dc804d6c1d5d05b47e1b466e3cc0c80d77f6ff0a7054c5201c184a682`;
- read-only API and `/catalog` Web route tests;
- Web build and 17/17 Web contract tests;
- repository `make check`: 1554 passed, 7 expected environment skips after the packaging repair;
- image-boundary test proving the Platform API image build context includes `data_catalog.py` and
  the exact Catalog authority closure.

The Catalog implementation commit is `d7e05cfa94aa41d12fbe5a48f80872bb8ee8e879`; the packaging
repair commit is `a15ae68daf5c254309bc7dcc73a88320cfd4c21c`.

## 2. Deployment failures

The governed production deploy was attempted only for `api` and `web`. Dry-run correctly planned:

```text
api  -> quanthus-platform-kernel:git-a15ae68
web  -> quanthus-platform-web:git-a15ae68
```

Actual deployment failed at the Web image/start path because Compose attempted to pull
`quanthus-platform-web` from a registry, which has no admitted remote repository/image. The failure
occurred after the API component may already have been replaced. The controlled deploy attempted its
rollback, but subsequent production status checks from both OCI and OCI MCP v2 fail on the same
rootful Docker container:

```text
Container 254da5a97612dd5e69f99af85a95ad7ca8f2b8de97f867bd759219e9e1c05a08 is restarting
```

Therefore the production rollback cannot currently be claimed healthy or complete.

## 3. Important environment distinction

DevSpace shell Docker uses the rootless endpoint:

```text
unix:///run/user/996/docker.sock
```

Quanthus governed production uses the rootful endpoint:

```text
/var/run/docker.sock
```

The DevSpace shell cannot access the rootful socket (`permission denied`), and the admitted MCP
production tools currently expose no rootful log/restart primitive. Repeated production deploy calls
are blocked by the same status preflight while the container is restarting.

## 4. Disk cleanup performed on the DevSpace/rootless Docker host

The user explicitly requested cleanup of expired/unused Docker images. Cleanup was limited to images
not referenced by any container, then two 3-day-old exited Slidethus full-test containers and their
now-unused images. No running container or Docker volume was deleted.

After cleanup the root filesystem showed approximately 5.6 GiB free. The remaining rootless images
are all referenced by currently running WFF/Slidethus containers.

This cleanup is independent of the rootful Quanthus production Docker daemon and does not constitute
production recovery.

## 5. Required closeout sequence

Do not close `P3-DICT-001-E` or `P3-DICT-001` until all of the following are true:

1. identify and recover the rootful restarting Quanthus container without modifying DWD;
2. verify production health/ready and a clean production checkout;
3. repair the Web deployment image-selection/pull behavior so Git-tag local images are used without
   an unintended registry pull;
4. deploy exact `origin/main` API + Web through the governed production tool;
5. smoke the authenticated Catalog summary/search/detail/relations API and `/catalog` route;
6. record final production evidence and only then close #227 followed by #215.

No Catalog formula, DWD schema, Facts v2 semantic, execution authority or trading behavior is changed
by this blocker record.

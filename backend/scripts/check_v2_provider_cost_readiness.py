from __future__ import annotations

import json

from app.db.session import SessionLocal
from app.services.provider_cost_readiness import build_provider_cost_readiness


def main() -> int:
    with SessionLocal() as session:
        readiness = build_provider_cost_readiness(session)
    print(json.dumps(readiness.model_dump(mode='json'), ensure_ascii=False, sort_keys=True))
    return 0 if readiness.ready_for_recording else 1


if __name__ == '__main__':
    raise SystemExit(main())

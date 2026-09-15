"""Demo data for local development. Never imported by the production Worker."""

from __future__ import annotations

import random

#: A soi in Bang Phli where several households report separately — the case that
#: should collapse into one dispatch rather than several.
CLUSTER_CENTRE = (13.6012, 100.7015)

SCENARIOS = [
    (["medical", "elderly"], 2, 90, "ผู้สูงอายุมีอาการหอบ ต้องการความช่วยเหลือด่วน"),
    (["rescue_boat", "infant"], 5, 140, "มีเด็กเล็ก น้ำขึ้นเร็ว"),
    (["food", "water"], 4, 60, "อาหารหมด"),
    (["trapped"], 3, 170, "ออกจากบ้านไม่ได้"),
    (["dialysis"], 1, 80, "ต้องฟอกไตพรุ่งนี้"),
    (["food"], 2, 40, None),
    (["evacuation", "disabled"], 3, 120, "ผู้พิการ เคลื่อนย้ายเอง ไม่ได้"),
    (["water", "power"], 6, 50, None),
]


def seed(db, submit_fn) -> int:
    random.seed(7)
    made = 0
    for i, (needs, people, depth, note) in enumerate(SCENARIOS):
        # Half land in one soi so clustering has something real to find.
        if i % 2 == 0:
            lat = CLUSTER_CENTRE[0] + random.uniform(-0.0004, 0.0004)
            lon = CLUSTER_CENTRE[1] + random.uniform(-0.0004, 0.0004)
            district, province = "บางพลี", "สมุทรปราการ"
        else:
            lat = 13.60 + random.uniform(-0.25, 0.25)
            lon = 100.70 + random.uniform(-0.25, 0.25)
            district, province = "เมือง", "สมุทรปราการ"
        status, _ = submit_fn(db, {
            "lat": lat, "lon": lon, "needs": needs, "people_count": people,
            "water_depth_cm": depth, "note": note, "consent": True,
            "contact_name": f"ผู้แจ้ง {i + 1}", "contact_phone": f"08{i}1234567",
            "province": province, "district": district,
            "client_id": f"seed-{i}",
        }, ip_hash=f"seed-ip-{i}")   # distinct hashes so seeding is not rate-limited
        made += 1 if status == 201 else 0
    return made

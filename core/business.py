# ruff: noqa: E501  (data tables: business definitions read better unwrapped)
"""Synthetic businesses: one per demo tenant, with workers, business objects, records and policies.

Invariants:
- Deterministic: every value comes from an RNG seeded by (seed, tenant), never from the clock.
- Additive: `seed_business` runs after `seed_demo` and does not change tenant IDs, canaries or the
  stage 0-5 seed data that earlier evals depend on.
- Every tenant's records carry that tenant's canary (in its asset tags), so a cross-tenant leak of
  records is detectable by string search, as for documents and metadata.
- Sensitive fields are marked `restricted` in the schema; enforcement is the reader's job
  (`dataqa`), not this module's.
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from core.store.models import FieldDef, ObjectDef, ObjectOverride
from core.store.sqlite_store import MetadataStore
from core.synthetic import SyntheticTenant

FIRST = (
    "Ava Ben Chloe Dev Elena Farid Grace Hiro Ines Jonah Kemi Liam Maya Nikhil Olivia Pedro Quinn "
    "Rosa Sami Tara Umar Vera Wes Ximena Yusuf Zoe Amir Bianca Cole Dana Emeka Fiona Gus Hana Ivan "
    "Jade Kofi Lena Marco Nadia"
).split()
LAST = (
    "Abbott Bose Carver Diaz Ekwueme Fischer Garcia Haddad Ito Jensen Kowalski Larsen Mensah "
    "Novak Okafor Patel Quinlan Rossi Sato Thorne Usman Varga Walsh Xu Yilmaz Zhang Ahmed Brennan "
    "Castillo Dubois"
).split()
CITIES = ["Northgate", "Riverside", "Old Town", "Harbor Point", "Eastfield", "Westbrook"]
BASE_DATE = dt.date(2026, 1, 1)  # fixed: data must not depend on when it is generated


@dataclass(frozen=True)
class BusinessDoc:
    doc_id: str
    title: str
    text: str


@dataclass
class BusinessProfile:
    tenant_id: str
    name: str
    kind: str
    description: str
    objects: list[str] = field(default_factory=list)
    docs: list[BusinessDoc] = field(default_factory=list)
    example_questions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Kind:
    kind: str
    name: str
    description: str
    # department -> [(job title, salary low, salary high[, hiring weight])], most senior title first
    departments: dict[str, list[tuple]]
    n_workers: int
    build: Callable[[random.Random, list[dict], str], dict[str, tuple[ObjectDef, list[dict]]]]
    docs: Callable[[random.Random, str], list[BusinessDoc]]
    questions: list[str]


def _person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def _day(rng: random.Random, lo: int, hi: int) -> str:
    return (BASE_DATE + dt.timedelta(days=rng.randint(lo, hi))).isoformat()


def _money(rng: random.Random, lo: float, hi: float) -> float:
    return round(rng.uniform(lo, hi), 2)


def _f(name: str, type_: str, **kw) -> FieldDef:
    return FieldDef(name=name, type=type_, **kw)


def _workers_of(workers: list[dict], *titles: str) -> list[dict]:
    pool = [w for w in workers if w["job_title"] in titles]
    return pool or workers


# ---- taxi -------------------------------------------------------------------------------------


def _taxi(rng: random.Random, workers: list[dict], ref: str) -> dict:
    vehicle_def = ObjectDef(
        name="vehicle",
        label="Vehicle",
        fields=[
            _f("plate", "string", required=True),
            _f("make", "string"),
            _f("model_year", "integer"),
            _f("status", "string"),
            _f("purchase_price", "decimal", restricted=True, label="Purchase price"),
        ],
    )
    makes = ["Toyota Prius", "Hyundai Ioniq", "Kia Niro", "Ford Transit", "Tesla Model 3"]
    vehicles = [
        {
            "id": f"vehicle-{i:04d}",
            "plate": f"TX-{rng.randint(1000, 9999)}",
            "make": rng.choice(makes),
            "model_year": rng.randint(2018, 2025),
            "status": rng.choices(["active", "maintenance", "retired"], [8, 1, 1])[0],
            "purchase_price": _money(rng, 24000, 52000),
        }
        for i in range(1, 15)
    ]
    ride_def = ObjectDef(
        name="ride",
        label="Ride",
        fields=[
            _f("ride_date", "date", required=True),
            _f("driver", "reference", reference_to="worker", required=True),
            _f("vehicle", "reference", reference_to="vehicle"),
            _f("pickup_zone", "string"),
            _f("dropoff_zone", "string"),
            _f("distance_km", "decimal"),
            _f("fare", "decimal"),
            _f("tip", "decimal"),
            _f("rating", "integer"),
            _f("payment_method", "string"),
        ],
    )
    drivers = _workers_of(workers, "Driver")
    zones = ["Airport", "Downtown", "University", "Harbor", "Stadium", "Suburbs"]
    rides = []
    for i in range(1, 221):
        km = round(rng.uniform(1.5, 32), 1)
        fare = round(3.5 + km * 1.85 * rng.choice([1, 1, 1, 1.4]), 2)
        rides.append(
            {
                "id": f"ride-{i:04d}",
                "ride_date": _day(rng, 0, 89),
                "driver": rng.choice(drivers)["id"],
                "vehicle": rng.choice(vehicles)["id"],
                "pickup_zone": rng.choice(zones),
                "dropoff_zone": rng.choice(zones),
                "distance_km": km,
                "fare": fare,
                "tip": round(fare * rng.choice([0, 0.1, 0.15, 0.2]), 2),
                "rating": rng.choices([5, 4, 3, 2, 1], [55, 25, 10, 6, 4])[0],
                "payment_method": rng.choice(["card", "card", "app", "cash"]),
            }
        )
    return {"vehicle": (vehicle_def, vehicles), "ride": (ride_def, rides)}


def _taxi_docs(rng: random.Random, ref: str) -> list[BusinessDoc]:
    per_km = 1.85
    surge = rng.choice([1.4, 1.5])
    return [
        BusinessDoc(
            "fare_policy",
            "Fare and surge policy",
            f"Fares are a 3.50 dollar flag fee plus {per_km} dollars per kilometre. During peak "
            f"demand a surge multiplier of {surge} applies, shown to the rider before booking. "
            f"(ref {ref})\n\nAirport pickups add a 5 dollar access fee. Drivers keep 100 percent of "
            f"tips. (ref {ref})",
        ),
        BusinessDoc(
            "driver_conduct",
            "Driver conduct and safety",
            f"Drivers rated below 3 stars on three rides in a week meet with the dispatch lead. "
            f"(ref {ref})\n\nVehicles in maintenance status must not be dispatched. (ref {ref})",
        ),
    ]


# ---- law firm ---------------------------------------------------------------------------------


def _law(rng: random.Random, workers: list[dict], ref: str) -> dict:
    client_def = ObjectDef(
        name="client",
        label="Client",
        fields=[
            _f("name", "string", required=True),
            _f("industry", "string"),
            _f("city", "string"),
            _f("retainer_balance", "decimal", restricted=True, label="Retainer balance"),
        ],
    )
    industries = ["Retail", "Healthcare", "Logistics", "Software", "Hospitality", "Real estate"]
    clients = [
        {
            "id": f"client-{i:04d}",
            "name": f"{rng.choice(LAST)} {rng.choice(['Holdings', 'Group', 'LLC', 'Partners', 'Inc'])}",
            "industry": rng.choice(industries),
            "city": rng.choice(CITIES),
            "retainer_balance": _money(rng, 0, 60000),
        }
        for i in range(1, 31)
    ]
    attorneys = _workers_of(workers, "Partner", "Associate")
    matter_def = ObjectDef(
        name="matter",
        label="Matter",
        fields=[
            _f("title", "string", required=True),
            _f("client", "reference", reference_to="client", required=True),
            _f("practice_area", "string"),
            _f("status", "string"),
            _f("opened_date", "date"),
            _f("lead_attorney", "reference", reference_to="worker"),
        ],
    )
    areas = ["Litigation", "Corporate", "Employment", "Real estate", "Family"]
    kinds = ["contract dispute", "acquisition", "lease review", "wrongful termination", "custody"]
    matters = [
        {
            "id": f"matter-{i:04d}",
            "title": f"{rng.choice(LAST)}: {rng.choice(kinds)}",
            "client": rng.choice(clients)["id"],
            "practice_area": rng.choice(areas),
            "status": rng.choices(["open", "closed", "on hold"], [6, 3, 1])[0],
            "opened_date": _day(rng, -300, 80),
            "lead_attorney": rng.choice(attorneys)["id"],
        }
        for i in range(1, 46)
    ]
    entry_def = ObjectDef(
        name="time_entry",
        label="Time entry",
        fields=[
            _f("matter", "reference", reference_to="matter", required=True),
            _f("attorney", "reference", reference_to="worker", required=True),
            _f("entry_date", "date"),
            _f("hours", "decimal"),
            _f("billable", "boolean"),
            _f("hourly_rate", "decimal", restricted=True, label="Hourly rate"),
        ],
    )
    rates = {w["id"]: rng.choice([250, 325, 400, 550, 700]) for w in attorneys}
    entries = []
    for i in range(1, 181):
        who = rng.choice(attorneys)
        entries.append(
            {
                "id": f"time_entry-{i:04d}",
                "matter": rng.choice(matters)["id"],
                "attorney": who["id"],
                "entry_date": _day(rng, 0, 89),
                "hours": round(rng.choice([0.5, 1, 1.5, 2, 3, 4, 6]) * 1.0, 1),
                "billable": rng.random() < 0.85,
                "hourly_rate": float(rates[who["id"]]),
            }
        )
    return {
        "client": (client_def, clients),
        "matter": (matter_def, matters),
        "time_entry": (entry_def, entries),
    }


def _law_docs(rng: random.Random, ref: str) -> list[BusinessDoc]:
    minimum = rng.choice([0.25, 0.1])
    return [
        BusinessDoc(
            "billing_policy",
            "Billing policy",
            f"Time is recorded in increments of {minimum} hours and billed monthly. Non-billable "
            f"time must still be entered with billable set to false. (ref {ref})",
        ),
        BusinessDoc(
            "conflicts_policy",
            "Conflict check policy",
            f"A conflict check against all clients and matters is required before opening a new "
            f"matter. The partner on the matter signs off. (ref {ref})",
        ),
    ]


# ---- dental office ----------------------------------------------------------------------------


def _dental(rng: random.Random, workers: list[dict], ref: str) -> dict:
    patient_def = ObjectDef(
        name="patient",
        label="Patient",
        fields=[
            _f("full_name", "string", required=True),
            _f("date_of_birth", "date", restricted=True, label="Date of birth"),
            _f("city", "string"),
            _f("insurance_provider", "string"),
            _f("clinical_notes", "string", restricted=True, label="Clinical notes"),
        ],
    )
    notes = ["sensitive to cold", "anxious; prefers morning visits", "penicillin allergy",
             "orthodontic referral pending", "no concerns"]  # fmt: skip
    patients = [
        {
            "id": f"patient-{i:04d}",
            "full_name": _person(rng),
            "date_of_birth": (
                dt.date(1950, 1, 1) + dt.timedelta(days=rng.randint(0, 25000))
            ).isoformat(),
            "city": rng.choice(CITIES),
            "insurance_provider": rng.choice(["BrightSmile", "DentaCare", "OpenBite", "self-pay"]),
            "clinical_notes": rng.choice(notes),
        }
        for i in range(1, 81)
    ]
    dentists = _workers_of(workers, "Dentist", "Hygienist")
    appt_def = ObjectDef(
        name="appointment",
        label="Appointment",
        fields=[
            _f("patient", "reference", reference_to="patient", required=True),
            _f("dentist", "reference", reference_to="worker"),
            _f("appointment_date", "date", required=True),
            _f("procedure", "string"),
            _f("fee", "decimal"),
            _f("status", "string"),
        ],
    )
    procedures = {"cleaning": 120, "filling": 210, "crown": 1100, "extraction": 260, "exam": 85}
    appointments = []
    for i in range(1, 191):
        proc = rng.choice(list(procedures))
        appointments.append(
            {
                "id": f"appointment-{i:04d}",
                "patient": rng.choice(patients)["id"],
                "dentist": rng.choice(dentists)["id"],
                "appointment_date": _day(rng, 0, 119),
                "procedure": proc,
                "fee": float(procedures[proc]),
                "status": rng.choices(
                    ["completed", "scheduled", "no-show", "cancelled"], [6, 2, 1, 1]
                )[0],
            }
        )
    return {"patient": (patient_def, patients), "appointment": (appt_def, appointments)}


def _dental_docs(rng: random.Random, ref: str) -> list[BusinessDoc]:
    fee = rng.choice([40, 50])
    return [
        BusinessDoc(
            "patient_privacy",
            "Patient privacy policy",
            f"Dates of birth and clinical notes are visible to the practice manager and clinical "
            f"leads only. Front desk staff see names, cities and insurance. (ref {ref})",
        ),
        BusinessDoc(
            "no_show_policy",
            "Missed appointment policy",
            f"A missed appointment without 24 hours notice incurs a {fee} dollar fee. Two no-shows "
            f"in a year require a deposit for the next booking. (ref {ref})",
        ),
    ]


# ---- construction -----------------------------------------------------------------------------


def _construction(rng: random.Random, workers: list[dict], ref: str) -> dict:
    pms = _workers_of(workers, "Project Manager", "Foreman")
    project_def = ObjectDef(
        name="project",
        label="Project",
        fields=[
            _f("name", "string", required=True),
            _f("client_name", "string"),
            _f("city", "string"),
            _f("status", "string"),
            _f("start_date", "date"),
            _f("budget", "decimal", restricted=True, label="Budget"),
            _f("project_manager", "reference", reference_to="worker"),
        ],
    )
    kinds = ["kitchen remodel", "office fit-out", "roof replacement", "warehouse extension",
             "school renovation", "clinic build"]  # fmt: skip
    projects = [
        {
            "id": f"project-{i:04d}",
            "name": f"{rng.choice(CITIES)} {rng.choice(kinds)}",
            "client_name": f"{rng.choice(LAST)} {rng.choice(['Properties', 'Co', 'Trust'])}",
            "city": rng.choice(CITIES),
            "status": rng.choices(["planning", "active", "complete"], [2, 5, 3])[0],
            "start_date": _day(rng, -200, 60),
            "budget": _money(rng, 40000, 2500000),
            "project_manager": rng.choice(pms)["id"],
        }
        for i in range(1, 26)
    ]
    equipment_def = ObjectDef(
        name="equipment",
        label="Equipment",
        fields=[
            _f("name", "string", required=True),
            _f("category", "string"),
            _f("daily_rate", "decimal"),
            _f("assigned_project", "reference", reference_to="project"),
            _f("status", "string"),
        ],
    )
    gear = {"excavator": "heavy", "scissor lift": "access", "concrete mixer": "heavy",
            "generator": "power", "scaffold set": "access", "laser level": "survey"}  # fmt: skip
    equipment = []
    for i in range(1, 41):
        name = rng.choice(list(gear))
        equipment.append(
            {
                "id": f"equipment-{i:04d}",
                "name": f"{name} #{i}",
                "category": gear[name],
                "daily_rate": _money(rng, 60, 900),
                "assigned_project": rng.choice(projects)["id"],
                "status": rng.choices(["on site", "in yard", "repair"], [6, 3, 1])[0],
            }
        )
    return {"project": (project_def, projects), "equipment": (equipment_def, equipment)}


def _construction_docs(rng: random.Random, ref: str) -> list[BusinessDoc]:
    hours = rng.choice([8, 10])
    return [
        BusinessDoc(
            "site_safety",
            "Site safety policy",
            f"Hard hats and high-visibility vests are required on every active site. Lifts are "
            f"inspected daily before use. (ref {ref})",
        ),
        BusinessDoc(
            "equipment_policy",
            "Equipment booking policy",
            f"Equipment in repair status cannot be assigned. Rentals over {hours} days need "
            f"project manager approval. (ref {ref})",
        ),
    ]


# ---- coffee roaster ---------------------------------------------------------------------------


def _coffee(rng: random.Random, workers: list[dict], ref: str) -> dict:
    product_def = ObjectDef(
        name="product",
        label="Product",
        fields=[
            _f("name", "string", required=True),
            _f("origin", "string"),
            _f("roast_level", "string"),
            _f("price", "decimal"),
            _f("unit_cost", "decimal", restricted=True, label="Unit cost"),
        ],
    )
    origins = ["Ethiopia", "Colombia", "Guatemala", "Kenya", "Sumatra", "Brazil", "Peru"]
    products = []
    for i in range(1, 15):
        origin = rng.choice(origins)
        price = float(rng.choice([14, 16, 18, 21, 24]))
        products.append(
            {
                "id": f"product-{i:04d}",
                "name": f"{origin} {rng.choice(['Sunrise', 'Reserve', 'Daily', 'Espresso'])}",
                "origin": origin,
                "roast_level": rng.choice(["light", "medium", "dark"]),
                "price": price,
                "unit_cost": round(price * rng.uniform(0.35, 0.55), 2),
            }
        )
    sale_def = ObjectDef(
        name="sale",
        label="Sale",
        fields=[
            _f("sale_date", "date", required=True),
            _f("product", "reference", reference_to="product", required=True),
            _f("quantity", "integer"),
            _f("channel", "string"),
            _f("store_location", "string"),
            _f("revenue", "decimal"),
        ],
    )
    sales = []
    for i in range(1, 241):
        product = rng.choice(products)
        qty = rng.randint(1, 4) if rng.random() < 0.7 else rng.randint(10, 60)
        sales.append(
            {
                "id": f"sale-{i:04d}",
                "sale_date": _day(rng, 0, 89),
                "product": product["id"],
                "quantity": qty,
                "channel": "wholesale" if qty >= 10 else rng.choice(["cafe", "online"]),
                "store_location": rng.choice(["Old Town", "Harbor Point", "online"]),
                "revenue": round(qty * product["price"] * (0.7 if qty >= 10 else 1), 2),
            }
        )
    return {"product": (product_def, products), "sale": (sale_def, sales)}


def _coffee_docs(rng: random.Random, ref: str) -> list[BusinessDoc]:
    discount = rng.choice([25, 30])
    return [
        BusinessDoc(
            "wholesale_policy",
            "Wholesale pricing policy",
            f"Wholesale orders of 10 bags or more receive a {discount} percent discount off the "
            f"retail price. (ref {ref})",
        ),
        BusinessDoc(
            "freshness_policy",
            "Freshness policy",
            f"Beans are sold within 21 days of roasting; older stock is donated. (ref {ref})",
        ),
    ]


KINDS: dict[str, _Kind] = {
    "fjord": _Kind(
        "taxi", "Fjord Taxi Co.", "A city taxi company: drivers, vehicles and rides.",
        {"Driving": [("Driver", 36000, 58000, 12)],
         "Dispatch": [("Dispatch Lead", 58000, 72000), ("Dispatcher", 40000, 52000)],
         "Maintenance": [("Mechanic", 48000, 66000)],
         "Office": [("Operations Manager", 78000, 98000), ("Bookkeeper", 45000, 58000)]}, 24, _taxi, _taxi_docs,
        ["How many rides were there by pickup zone?", "What is the average fare by payment method?",
         "Which vehicles are in maintenance?", "What are the average salaries by department?"],
    ),
    "cinder": _Kind(
        "law", "Cinder & Partners LLP", "A law firm: attorneys, clients, matters and billable time.",
        {"Litigation": [("Partner", 240000, 340000), ("Associate", 120000, 190000),
                        ("Paralegal", 58000, 78000)],
         "Corporate": [("Partner", 250000, 360000), ("Associate", 125000, 200000)],
         "Operations": [("Office Manager", 72000, 95000), ("Legal Secretary", 50000, 64000)]}, 18, _law, _law_docs,
        ["How many open matters are there by practice area?", "Total hours logged by attorney?",
         "Which clients are in the Software industry?", "What is the average hourly rate?"],
    ),
    "gable": _Kind(
        "dental", "Gable Family Dental", "A dental practice: clinicians, patients and appointments.",
        {"Clinical": [("Dentist", 165000, 230000), ("Dental Assistant", 40000, 52000)],
         "Hygiene": [("Hygienist", 78000, 98000)],
         "Front Desk": [("Practice Manager", 68000, 88000), ("Receptionist", 36000, 46000)]}, 14, _dental, _dental_docs,
        ["How many appointments are there by procedure?", "What is the total fee for completed appointments?",
         "How many patients use each insurance provider?", "List patients with their dates of birth"],
    ),
    "juniper": _Kind(
        "construction", "Juniper Builders", "A construction contractor: crews, projects and equipment.",
        {"Field Crew": [("Foreman", 82000, 105000), ("Carpenter", 52000, 72000),
                        ("Electrician", 60000, 84000)],
         "Estimating": [("Estimator", 70000, 92000)],
         "Office": [("Project Manager", 95000, 130000), ("Office Admin", 44000, 56000)]}, 20, _construction, _construction_docs,
        ["How many projects are in each status?", "What equipment is in repair?",
         "What is the average equipment daily rate by category?", "What is the total budget of active projects?"],
    ),
    "acme": _Kind(
        "coffee", "Acme Coffee Roasters", "A coffee roaster with cafes: staff, products and sales.",
        {"Roastery": [("Head Roaster", 62000, 78000), ("Roaster", 42000, 52000)],
         "Cafe": [("Cafe Manager", 52000, 64000), ("Barista", 31000, 38000)],
         "Wholesale": [("Sales Rep", 48000, 70000)], "Office": [("Bookkeeper", 46000, 58000)]}, 16, _coffee, _coffee_docs,
        ["What is total revenue by channel?", "Which products are dark roast?",
         "How many bags were sold per store location?", "What is the average unit cost by origin?"],
    ),
}  # fmt: skip

_WORKER_FIELDS = [
    FieldDef(name="department", type="string", label="Department"),
    FieldDef(name="job_title", type="string", label="Job title"),
    FieldDef(name="badge_number", type="string", label="Badge number"),
    FieldDef(name="salary", type="decimal", label="Salary", restricted=True),
]


def _value_for(f: FieldDef, rng: random.Random, workers: list[dict]):
    if f.type == "string":
        return rng.choice(["north", "south", "east", "west", "central"])
    if f.type == "integer":
        return rng.randint(1, 5)
    if f.type == "decimal":
        return _money(rng, 1, 100)
    if f.type == "boolean":
        return rng.random() < 0.2
    if f.type == "date":
        return _day(rng, -100, 100)
    return rng.choice(workers)["id"] if workers else None


def _make_workers(kind: _Kind, rng: random.Random, prefix: str) -> list[dict]:
    titles = [(dept, t) for dept, ts in kind.departments.items() for t in ts]
    workers: list[dict] = []
    for i in range(1, kind.n_workers + 1):
        # The first title of each department is filled once, as its head; the rest are spread.
        # Every title appears at least once; the rest are drawn with the junior titles weighted up.
        if i <= len(titles):
            dept, spec = titles[i - 1]
        else:
            weights = [
                spec[3] if len(spec) > 3 else (1 if rank == 0 and len(ts) > 1 else 3)
                for ts in kind.departments.values()
                for rank, spec in enumerate(ts)
            ]
            dept, spec = rng.choices(titles, weights)[0]
        title, lo, hi = spec[:3]
        salary = rng.uniform(lo, hi)
        workers.append(
            {
                "id": f"worker-{i:04d}",
                "full_name": _person(rng),
                "hire_date": _day(rng, -3000, -10),
                "manager": None,
                "notes": "performance review due",  # hidden by every tenant's override
                "department": dept,
                "job_title": title,
                "badge_number": f"{prefix}-{rng.randint(10000, 99999)}",
                "salary": round(salary / 100) * 100.0,
            }
        )
    heads = {
        w["department"]: w["id"]
        for w in reversed(workers)
        if w["job_title"] == kind.departments[w["department"]][0][0]
    }
    for w in workers:
        head = heads.get(w["department"])
        w["manager"] = head if head != w["id"] else None
    return workers


def seed_business(
    store: MetadataStore, tenants: list[SyntheticTenant], seed: int = 7
) -> dict[str, BusinessProfile]:
    """Turn each seeded tenant into a business with objects, records and policy documents."""
    profiles: dict[str, BusinessProfile] = {}
    for tenant in tenants:
        kind = KINDS.get(tenant.tenant_id)
        if kind is None:
            continue
        ctx = tenant.context()
        rng = random.Random(f"business:{seed}:{tenant.tenant_id}")

        # Workers: extend the platform worker object for this tenant (merged onto its override).
        current = store.get_override(ctx, "worker") or ObjectOverride()
        have = {f.name for f in current.add_fields}
        store.put_override(
            ctx,
            "worker",
            ObjectOverride(
                add_fields=[
                    *current.add_fields,
                    *(f for f in _WORKER_FIELDS if f.name not in have),
                ],
                hide_fields=current.hide_fields,
                relabel=current.relabel,
            ),
        )
        workers = _make_workers(kind, rng, tenant.tenant_id[:2].upper())
        for extra in current.add_fields:  # values for the tenant's stage-0 extra fields too
            for w in workers:
                w[extra.name] = _value_for(extra, rng, workers)
        store.put_records(ctx, "worker", workers)

        # Assets (stage 0 custom object): records carry the canary.
        assets = [
            {
                "id": f"asset-{i:04d}",
                "tag": tenant.canary if i == 1 else f"AT-{rng.randint(100, 999)}",
                "assigned_to": rng.choice(workers)["id"],
            }  # fmt: skip
            for i in range(1, 9)
        ]
        store.put_records(ctx, f"{tenant.tenant_id}_asset", assets)

        names = ["worker", f"{tenant.tenant_id}_asset"]
        for name, (obj, records) in kind.build(rng, workers, tenant.canary).items():
            store.put_object(ctx, obj)
            store.put_records(ctx, name, records)
            names.append(name)

        profiles[tenant.tenant_id] = BusinessProfile(
            tenant_id=tenant.tenant_id,
            name=kind.name,
            kind=kind.kind,
            description=kind.description,
            objects=names,
            docs=kind.docs(rng, tenant.canary),
            example_questions=list(kind.questions),
        )
    return profiles

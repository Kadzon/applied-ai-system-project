"""PawPal+ AI - the web app end users open.  Run with:  streamlit run app.py"""
from __future__ import annotations

from datetime import date, datetime, time

import streamlit as st

from app_logging import setup_logging
from llm import GeminiClient
from main import build_sample_owner
from models import CATEGORIES, Frequency, HealthReport, MissedDose, Owner, Pet, Priority, Task
from retriever import KnowledgeBase
from scheduler import Plan
from workflow import run_care_workflow

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SYMPTOMS = {"vomiting": "Vomiting", "diarrhea": "Diarrhea", "lethargy": "Unusual tiredness",
            "not_eating": "Skipped meals"}
ALERT_BOX = {"urgent": st.error, "warning": st.warning, "info": st.info}

setup_logging()
st.set_page_config(page_title="PawPal+ AI", page_icon="🐾", layout="wide")


@st.cache_resource
def get_kb() -> KnowledgeBase:
    return KnowledgeBase()


@st.cache_resource
def get_llm() -> GeminiClient:
    return GeminiClient()


if "owner" not in st.session_state:
    st.session_state.owner = Owner(name="Me")
owner: Owner = st.session_state.owner
llm = get_llm()


def show_plan(title: str, plan: Plan) -> None:
    st.subheader(title)
    if plan.scheduled:
        st.table([{"Time": f"{s.start_time:%H:%M}-{s.end_time:%H:%M}", "Pet": s.pet.name,
                   "Task": s.task.title, "Why": s.reason} for s in plan.scheduled])
    else:
        st.write("Nothing scheduled.")
    if plan.skipped:
        st.markdown("**Not scheduled**")
        for s in plan.skipped:
            st.write(f"- {s.pet.name}: {s.task.title} - {s.reason}")


# ------------------------------------------------------------------ header + sidebar
st.title("🐾 PawPal+ AI")
st.caption("Tell me how your pet is doing today and I'll adjust the care plan. "
           "Not veterinary advice - call your vet when in doubt.")

with st.sidebar:
    st.header("Today")
    day = st.date_input("Date", value=date.today())
    now_time = st.time_input("Current time", value=datetime.now().time().replace(second=0, microsecond=0))
    budget = st.slider("Time available for pet care (minutes)", 30, 480, 180, step=15)
    use_gemini = st.toggle("Use Gemini for a second opinion", value=llm.available, disabled=not llm.available)
    st.caption("Gemini is on, and the safety rules still apply." if llm.available and use_gemini else
               "Running on the built-in safety rules." if llm.available else
               "No GEMINI_API_KEY found, so the built-in safety rules are used.")
    owner.name = st.text_input("Your name", value=owner.name)
    if st.button("Load sample pets"):
        st.session_state.owner = build_sample_owner()
        st.rerun()
    if st.button("Clear everything"):
        st.session_state.owner = Owner(name="Me")
        st.session_state.pop("result", None)
        st.rerun()

tab_pets, tab_checkin = st.tabs(["1. My pets and tasks", "2. Daily check-in and plan"])

# ------------------------------------------------------------------ tab 1: pets and tasks
with tab_pets:
    left, right = st.columns(2)
    with left:
        st.subheader("Add a pet")
        with st.form("add_pet", clear_on_submit=True):
            pet_name = st.text_input("Name")
            species = st.selectbox("Species", ["dog", "cat"])
            breed = st.text_input("Breed (optional)")
            if st.form_submit_button("Add pet"):
                if not pet_name.strip():
                    st.error("Please enter a name.")
                elif owner.find_pet(pet_name):
                    st.error(f"You already have a pet named {pet_name}.")
                else:
                    owner.add_pet(Pet(pet_name.strip(), species, breed.strip()))
                    st.rerun()

    with right:
        st.subheader("Add a task")
        if not owner.pets:
            st.info("Add a pet first.")
        else:
            with st.form("add_task", clear_on_submit=True):
                for_pet = st.selectbox("For", [p.name for p in owner.pets])
                title = st.text_input("Task (e.g. Morning walk)")
                c1, c2, c3 = st.columns(3)
                minutes = c1.number_input("Minutes", 1, 240, 15)
                priority = c2.selectbox("Priority", [p.value for p in Priority], index=1)
                category = c3.selectbox("Type", sorted(CATEGORIES), index=sorted(CATEGORIES).index("general"))
                medication = st.text_input("Medication name (only for type 'medication')")
                freq = st.radio("Repeats", [f.value for f in Frequency], horizontal=True)
                days = st.multiselect("On which days (weekly only)", DAYS)
                timed = st.checkbox("Happens at a fixed time (appointments, timed pills)")
                at = st.time_input("Fixed time", value=time(9, 0))
                if st.form_submit_button("Add task"):
                    try:
                        owner.find_pet(for_pet).add_task(Task(
                            title=title.strip() or "Untitled task", duration_minutes=int(minutes),
                            priority=Priority(priority), frequency=Frequency(freq),
                            weekdays={DAYS.index(d) for d in days}, category=category,
                            fixed_time=at if timed else None,
                            medication=medication.strip() or None))
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))  # e.g. weekly with no days, medication with no name

    st.divider()
    if not owner.pets:
        st.write("No pets yet. Add one above, or click **Load sample pets** in the sidebar.")
    for pet in owner.pets:
        st.markdown(f"**{pet.name}** - {pet.species}{', ' + pet.breed if pet.breed else ''}")
        if pet.tasks:
            st.table([{"Task": t.title, "Type": t.category, "Minutes": t.duration_minutes,
                       "Priority": t.priority.value,
                       "When": t.fixed_time.strftime("%H:%M") if t.fixed_time else "flexible",
                       "Repeats": t.frequency.value if t.frequency == Frequency.DAILY
                       else "weekly: " + ", ".join(DAYS[d] for d in sorted(t.weekdays))} for t in pet.tasks])
        else:
            st.caption("No tasks yet.")

# ------------------------------------------------------------------ tab 2: check-in and plan
with tab_checkin:
    if not owner.pets:
        st.info("Add a pet and some tasks first (tab 1).")
        st.stop()

    pet = owner.find_pet(st.selectbox("Which pet are you checking in on?", [p.name for p in owner.pets]))
    st.subheader(f"How is {pet.name} today?")
    cols = st.columns(len(SYMPTOMS))
    symptoms = {key: cols[i].number_input(f"{label} (times today)", 0, 20, 0, key=f"sym_{pet.name}_{key}")
                for i, (key, label) in enumerate(SYMPTOMS.items())}

    meds = [t for t in pet.tasks if t.category == "medication"]
    missed: list[MissedDose] = []
    if meds:
        st.markdown("**Missed any medication?**")
        for t in meds:
            if st.checkbox(f"{t.title} was missed", key=f"miss_{pet.name}_{t.title}"):
                due_at = st.time_input("It was due at", value=t.fixed_time or time(8, 0),
                                       key=f"missat_{pet.name}_{t.title}")
                missed.append(MissedDose(t.medication, datetime.combine(day, due_at)))
    notes = st.text_area("Anything else? (optional)", placeholder="e.g. seems a bit tired")

    if st.button("Build today's plan", type="primary"):
        report = HealthReport(pet.name, {k: v for k, v in symptoms.items() if v}, missed, notes)
        with st.spinner("Checking guidelines and building your plan..."):
            st.session_state.result = run_care_workflow(
                owner, report, day, datetime.combine(day, now_time), budget,
                kb=get_kb(), llm=llm if use_gemini else None)

    result = st.session_state.get("result")
    if result:
        st.divider()
        sev = result.triage.severity.replace("_", " ")
        conf = result.confidence
        st.subheader(f"Today's plan - health status: {sev}")
        st.metric("How sure am I?", f"{conf.label.capitalize()} ({conf.score:.0%})",
                  help="Based on whether a guideline matched, whether Gemini agreed with the rules, "
                       "and whether the plan passed its safety check.")
        for alert in result.plan.alerts:
            ALERT_BOX[alert.level](alert.message)
        if result.triage.severity in ("see_vet", "urgent"):
            st.warning("I can't diagnose your pet. Please follow the vet advice above.")
        if not result.plan.alerts:
            st.success("Nothing unusual - here is your normal plan.")

        before, after = st.columns(2)
        with before:
            show_plan("Without the AI", result.baseline_plan)
        with after:
            show_plan("With the AI", result.plan)
        with st.expander("How was this decided?"):
            st.write("Guidance used: " + (", ".join(result.sources) or "none needed"))
            st.write("Gemini second opinion: " + ("used" if result.triage.used_llm else "not used"))
            st.write("Safety check: " + ("passed" if not result.issues else "; ".join(result.issues)))
            st.write(f"**Confidence {result.confidence.score:.2f} ({result.confidence.label}) because:**")
            for reason in result.confidence.reasons:
                st.write(f"- {reason}")
            st.caption("Every decision is also written to logs/pawpal_ai.log.")

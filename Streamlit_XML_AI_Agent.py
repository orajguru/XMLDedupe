import streamlit as st
import xml.etree.ElementTree as ET
import pandas as pd
from io import BytesIO
import os

# Try to import ai_engine if present (user provided)
try:
    from ai_engine import AIEngine
    llm = AIEngine()
except Exception:
    llm = None

st.set_page_config(page_title="XML AI Mapper", page_icon="🤖", layout="wide")
st.title("🔍 XML Field Mapper (AI Powered)")
st.caption("Upload → Clean → Compare → Ask AI → Export")

# Show which AI engine is active (if ai_engine exists)
if llm and llm.active_model:
    st.sidebar.success(f"🧠 Model in use: {llm.active_model}")
else:
    st.sidebar.warning("⚠️ AI model not initialized yet.")

# ------------------- Helper functions (correct algorithm) -------------------

def _split_field(text):
    """Split comma-separated attribute safely and return stripped tokens."""
    if not text:
        return []
    return [t.strip() for t in text.split(",") if t.strip()]

def aggregate_per_name(root):
    """
    For each individual name (exploded from option/@name), collect:
      - values: set of values for that name across options
      - dependents: set of (id, name) tuples across options
    Also record first appearance index of name for stable ordering.
    Returns: per_name dict, name_first_index dict
    """
    per_name = {}
    name_first_index = {}
    appearance_counter = 0

    for opt in root.findall("option"):
        names = _split_field(opt.get("name", ""))
        values = _split_field(opt.get("value", ""))

        # dependents in this option
        deps = []
        for d in opt.findall("dependent"):
            dep_id = d.get("id")
            dep_name = d.get("name")
            deps.append((dep_id, dep_name))

        # assign each name a corresponding value if available
        for i, name in enumerate(names):
            # if fewer values than names, last value used for remaining names (safe fallback)
            value = values[i] if i < len(values) else (values[-1] if values else "")
            if name not in per_name:
                per_name[name] = {"values": set(), "dependents": set()}
            per_name[name]["values"].add(value)
            per_name[name]["dependents"].update(deps)

            # record first appearance
            if name not in name_first_index:
                name_first_index[name] = appearance_counter
                appearance_counter += 1

    return per_name, name_first_index

def group_by_deps(per_name, name_first_index):
    """
    Group names that have exactly identical dependent sets.
    Returns ordered list of groups (each group: names, values, dependents, order_key)
    """
    groups_map = {}  # key: deps_key (tuple) -> aggregated data

    for name, info in per_name.items():
        deps_key = tuple(sorted(info["dependents"]))
        if deps_key not in groups_map:
            groups_map[deps_key] = {"names": set(), "values": set(), "dependents": list(sorted(info["dependents"]))}
        groups_map[deps_key]["names"].add(name)
        groups_map[deps_key]["values"].update(info["values"])

    # Turn into list with order_key computed from earliest name appearance
    groups = []
    for deps_key, data in groups_map.items():
        order_key = min(name_first_index.get(n, 10**9) for n in data["names"])
        groups.append({
            "names": sorted(data["names"], key=lambda x: name_first_index.get(x, 10**9)),
            "values": sorted(data["values"], key=lambda v: (int(v) if v.isdigit() else v)),
            "dependents": data["dependents"],
            "order_key": order_key
        })

    # Sort groups by order_key to preserve stable ordering based on first appearance
    groups.sort(key=lambda g: g["order_key"])
    return groups

def _prettify_xml(elem):
    """
    Simple pretty printer to indent XML for readability.
    """
    def _indent(e, level=0):
        i = "\n" + level*"  "
        if len(e):
            if not e.text or not e.text.strip():
                e.text = i + "  "
            for child in e:
                _indent(child, level+1)
            if not child.tail or not child.tail.strip():
                child.tail = i
        if level and (not e.tail or not e.tail.strip()):
            e.tail = i
    _indent(elem)
    return ET.tostring(elem, encoding="unicode")

def generate_clean_xml_from_root(root):
    """
    Full pipeline:
      - aggregate per name
      - group names by identical dependent sets
      - build new <dependents> XML preserving original attributes
    """
    per_name, name_first_index = aggregate_per_name(root)
    groups = group_by_deps(per_name, name_first_index)

    new_root = ET.Element("dependents", root.attrib)

    for g in groups:
        opt = ET.SubElement(new_root, "option")
        opt.set("name", ",".join(g["names"]))
        opt.set("value", ",".join(g["values"]))
        # append dependents in stable order
        for dep_id, dep_name in g["dependents"]:
            ET.SubElement(opt, "dependent", {
                "type": "0",
                "id": dep_id,
                "name": dep_name,
                "reset": "false",
                "retainonedit": "false"
            })

    return _prettify_xml(new_root)

# ------------------- Streamlit app UI -------------------

uploaded = st.file_uploader("📁 Upload XML file", type=["xml"])
xml_text = None
cleaned_xml = None
original_root = None

if uploaded:
    xml_text = uploaded.read().decode("utf-8")
    st.subheader("📄 Original XML Preview")
    st.code("\n".join(xml_text.splitlines()[:10]) + ("\n..." if len(xml_text.splitlines())>10 else ""), language="xml")


    # parse and clean
    try:
        original_root = ET.fromstring(xml_text)
        cleaned_xml = generate_clean_xml_from_root(original_root)
        st.subheader("🧼 Cleaned / Optimized XML Preview (Max 50 lines)")
        cleaned_lines = cleaned_xml.splitlines()
        preview_lines = cleaned_lines[:50]
        st.code("\n".join(preview_lines) + ("\n..." if len(cleaned_lines) > 50 else ""), language="xml")

        st.success("✅ Cleaned output generated (per-name aggregation & grouping by identical dependents).")

    except Exception as e:
        st.error(f"XML parse / cleaning error: {e}")

# Comparison counts
if uploaded and cleaned_xml:
    st.subheader("🔄 Summary (Before vs After)")
    df = pd.DataFrame([
        ["Option Count", len(original_root.findall('option')), cleaned_xml.count("<option")],
        ["Dependent Count", xml_text.count("<dependent"), cleaned_xml.count("<dependent")]
    ], columns=["Metric", "Original", "Cleaned"])
    st.dataframe(df)

# Download cleaned xml
if cleaned_xml:
    st.download_button("📥 Download Clean XML", cleaned_xml, file_name="cleaned_dependents.xml", mime="text/xml")

# ------------------- Export Mapping to Excel (Expanded per Option) -------------------
if cleaned_xml:
    st.subheader("📊 Export Option Mapping to Excel")

    root = ET.fromstring(cleaned_xml)

    mapping_rows = []
    group_number = 1

    for opt in root.findall("option"):

        # Group ID stays same for all exploded rows from this <option>
        group_id = f"G{group_number}"  

        # Split multi names & values
        names = opt.get("name", "").split(",")
        values = opt.get("value", "").split(",")

        # Collect dependents once
        dependents = []
        for d in opt.findall("dependent"):
            dep_id = d.get("id", "")
            dep_name = d.get("name", "")
            dependents.append(f"{dep_id}:{dep_name}")
        dependents_str = ";".join(dependents)

        # Expand rows → match value count safely
        for idx, name in enumerate(names):
            name = name.strip()

            # If values fewer than names → reuse last
            if idx < len(values):
                val = values[idx].strip()
            else:
                val = values[-1].strip() if values else ""

            mapping_rows.append([
                len(mapping_rows) + 1,
                group_id,
                name,
                val,
                dependents_str
            ])

        group_number += 1

    # Create DataFrame
    df_export = pd.DataFrame(mapping_rows, columns=[
        "Sr No", "Group ID", "Option Name", "Option Value", "Dependents"
    ])

    excel_buffer = BytesIO()
    df_export.to_excel(excel_buffer, index=False, sheet_name="Mapping")
    excel_buffer.seek(0)

    st.download_button(
        "📥 Download Mapping Excel",
        data=excel_buffer,
        file_name="option_mapping.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# AI Suggest mapping (if ai_engine present)
st.markdown("---")
st.subheader("🤖 AI: Suggest Mapping (Optional)")

if st.button("💡 Suggest Mapping (AI)"):
    if not cleaned_xml:
        st.warning("Please upload and generate cleaned XML first.")
    else:
        if not llm:
            st.warning("No AI engine available (ensure ai_engine.py exists and secrets set).")
        else:
            with st.spinner("AI analyzing cleaned XML..."):
                try:
                    # generate prompt & call
                    prompt = f"""You are an XML expert. Given the cleaned <dependents> XML below,
explain grouping decisions, detect duplicates, and produce a suggested mapping table.
Return a short summary and a JSON mapping example.

Cleaned XML:
{cleaned_xml}
"""
                    ai_text = llm.generate(prompt)
                    st.subheader("AI Output")
                    st.code(ai_text)
                except Exception as e:
                    st.error(f"AI call error: {e}")


st.caption("Built by IBL Digital Team • AI XML Mapping Assistant 🔧🚀")

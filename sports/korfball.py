import math
import io
import json
import sqlite3
import zipfile
from datetime import datetime, date
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as PDFImage

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from streamlit_image_coordinates import streamlit_image_coordinates

FIELD_IMAGE_SIZE = 700
FIELD_MARGIN = 30
FIELD_METRES = 20
POST_X = 10.0
POST_Y = 6.67
SHOT_TYPES = ["Long shot", "Short shot", "Running in shot", "Goalzone shot", "Free shot", "Penalty"]
DISTANCE_RINGS = (2, 5, 8)
COMPETITION_TYPES = ["Mixed - Standard", "Mixed - Recreational", "Women"]


def initialize_state():
    defaults = {
        "match_started": False, "team": "", "opponent": "", "match_date": None,
        "competition_type": "Mixed - Standard", "players": [], "player_genders": {},
        "starting_attack": [], "starting_defense": [], "starting_bench": [],
        "attack_players": [], "defense_players": [], "bench_players": [],
        "actions": [], "match_events": [], "our_score": 0, "opponent_score": 0,
        "goals_since_switch": 0, "zone_switches": 0,
        "shot_x": None, "shot_y": None, "field_key_number": 0,
        "clock_elapsed": 0.0, "clock_started_at": None, "clock_running": False,
        "match_id": None, "match_finished": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if "events_df" not in st.session_state:
        st.session_state.events_df = pd.DataFrame(columns=["Player", "Action", "X", "Y", "Half"])


def count_genders(players, genders):
    male = sum(genders[p] == "Male" for p in players)
    return male, len(players) - male


def validate_lineup(attack, defense, genders, competition_type):
    if len(attack) != 4 or len(defense) != 4:
        return False, "Each zone must contain 4 players."
    if len(set(attack + defense)) != 8:
        return False, "A player cannot occupy two positions."
    attack_male, _ = count_genders(attack, genders)
    defense_male, _ = count_genders(defense, genders)
    if competition_type == "Mixed - Standard" and (attack_male != 2 or defense_male != 2):
        return False, "Each zone requires 2 male and 2 female players."
    if competition_type == "Mixed - Recreational" and abs(attack_male - defense_male) > 1:
        return False, "Distribute male and female players as evenly as possible between the zones."
    if competition_type == "Women" and (attack_male != 0 or defense_male != 0):
        return False, "All players on the field must be female."
    return True, "Valid lineup"


def count_substitutions():
    return sum(event["type"] == "substitution" for event in st.session_state.match_events)


def get_substitution_limit():
    return 8 if st.session_state.competition_type == "Mixed - Standard" else None


def rebuild_match_state():
    attack = st.session_state.starting_attack.copy()
    defense = st.session_state.starting_defense.copy()
    bench = st.session_state.starting_bench.copy()
    our_score = opponent_score = goals_since_switch = zone_switches = 0
    actions = []
    for event in st.session_state.match_events:
        if event["type"] == "shot":
            actions.append(event)
            if event["result"] == "Goal":
                if event["team"] == "Our team":
                    our_score += 1
                else:
                    opponent_score += 1
                goals_since_switch += 1
                if goals_since_switch == 2:
                    attack, defense = defense, attack
                    goals_since_switch = 0
                    zone_switches += 1
        elif event["type"] == "substitution":
            outgoing, incoming = event["player_out"], event["player_in"]
            if outgoing in attack:
                attack[attack.index(outgoing)] = incoming
            elif outgoing in defense:
                defense[defense.index(outgoing)] = incoming
            else:
                raise ValueError(f"Cannot replay substitution: {outgoing} is not on the field")
            bench.remove(incoming)
            bench.append(outgoing)
    st.session_state.attack_players = attack
    st.session_state.defense_players = defense
    st.session_state.bench_players = bench
    st.session_state.our_score = our_score
    st.session_state.opponent_score = opponent_score
    st.session_state.goals_since_switch = goals_since_switch
    st.session_state.zone_switches = zone_switches
    st.session_state.actions = actions


def add_match_event(event):
    if st.session_state.match_finished:
        st.warning("This match is finished. Resume it before registering events.")
        return
    event["time_seconds"] = round(clock_seconds(), 1)
    st.session_state.match_events.append(event)
    rebuild_match_state()
    st.session_state.shot_x = None
    st.session_state.shot_y = None
    st.session_state.field_key_number += 1
    st.rerun()


def undo_last_event():
    if st.session_state.match_events:
        st.session_state.match_events.pop()
        rebuild_match_state()
        st.session_state.shot_x = None
        st.session_state.shot_y = None
        st.session_state.field_key_number += 1
        st.rerun()


def automatic_shot_type(distance):
    if distance < 2:
        return "Goalzone shot"
    if distance < 5:
        return "Short shot"
    return "Long shot"


def draw_distance_rings(draw, px, py, scale):
    for radius in DISTANCE_RINGS:
        r = radius * scale
        draw.ellipse((px - r, py - r, px + r, py + r), outline="#B0B0B0", width=2)
        tx = px + r + 3
        if tx < FIELD_IMAGE_SIZE - 12:
            draw.text((tx, py - 12), f"{radius} m", fill="#333333")


def create_korfball_field():
    size, margin = FIELD_IMAGE_SIZE, FIELD_MARGIN
    scale = (size - 2 * margin) / FIELD_METRES
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((margin, margin, size - margin, size - margin), outline="black", width=3)
    px = margin + POST_X * scale
    py = margin + POST_Y * scale
    draw_distance_rings(draw, px, py, scale)
    draw.ellipse((px - 7, py - 7, px + 7, py + 7), fill="black")
    if st.session_state.shot_x is not None and st.session_state.shot_y is not None:
        x = margin + st.session_state.shot_x * scale
        y = margin + st.session_state.shot_y * scale
        d = 9
        draw.line((x-d, y-d, x+d, y+d), fill="#D62828", width=4)
        draw.line((x-d, y+d, x+d, y-d), fill="#D62828", width=4)
    return image


def register_shot(team, player, shot_type, result):
    x, y = st.session_state.shot_x, st.session_state.shot_y
    if x is None or y is None:
        st.warning("Select a shot location first.")
        return
    eligible = st.session_state.attack_players if team == "Our team" else st.session_state.defense_players
    if player not in eligible:
        st.error("Selected player is not currently in the correct zone.")
        return
    add_match_event({
        "type": "shot", "team": team, "player": player,
        "shot_type": shot_type, "result": result,
        "x": round(x, 3), "y": round(y, 3),
        "distance": round(math.hypot(x - POST_X, y - POST_Y), 3),
    })


def register_other_action(team, player, action):
    x, y = st.session_state.shot_x, st.session_state.shot_y
    if x is None or y is None:
        st.warning("Select a location on the field first.")
        return
    if team == "Our team" and player not in st.session_state.attack_players + st.session_state.defense_players:
        st.error("The selected player is not on the field.")
        return
    add_match_event({
        "type": "action", "team": team, "player": player, "action": action,
        "x": round(x, 3), "y": round(y, 3),
        "distance": round(math.hypot(x - POST_X, y - POST_Y), 3),
    })


@st.dialog("Register match event")
def register_field_event_dialog():
    x, y = st.session_state.shot_x, st.session_state.shot_y
    distance = math.hypot(x - POST_X, y - POST_Y)
    st.write(f"Selected location: {distance:.1f} m from the basket")
    event_kind = st.radio("Event type", ["Shot", "Other action"], horizontal=True)
    team = st.radio("Team", ["Our team", "Opponent"], horizontal=True)
    if event_kind == "Shot":
        players = st.session_state.attack_players if team == "Our team" else st.session_state.defense_players
        if team == "Opponent":
            st.caption("Select the defender involved; this does not assign blame for a conceded goal.")
        player = st.selectbox("Shooter" if team == "Our team" else "Defender involved", players)
        default_type = automatic_shot_type(distance)
        st.info(f"Automatically classified: **{default_type}** ({distance:.1f} m)")
        special = st.checkbox("Special shot (running-in, free shot or penalty)", value=False)
        if special:
            shot_type = st.selectbox("Special shot type", ["Running in shot", "Free shot", "Penalty"])
        else:
            shot_type = default_type
        left, right = st.columns(2)
        with left:
            if st.button("Goal", width="stretch", disabled=st.session_state.match_finished):
                register_shot(team, player, shot_type, "Goal")
        with right:
            if st.button("Miss", width="stretch", disabled=st.session_state.match_finished):
                register_shot(team, player, shot_type, "Miss")
    else:
        action = st.selectbox("Action", OTHER_ACTIONS)
        if team == "Our team":
            player = st.selectbox("Player involved", st.session_state.attack_players + st.session_state.defense_players)
        else:
            player = "Opponent"
        if st.button("Register action", width="stretch", disabled=st.session_state.match_finished):
            register_other_action(team, player, action)
    if st.button("Cancel"):
        st.session_state.shot_x = None
        st.session_state.shot_y = None
        st.session_state.field_key_number += 1
        st.rerun()


def substitute_player(player_out, player_in):
    limit = get_substitution_limit()
    if limit is not None and count_substitutions() >= limit:
        st.error(f"Maximum number of substitutions reached ({limit}).")
        return
    attack = st.session_state.attack_players.copy()
    defense = st.session_state.defense_players.copy()
    bench = st.session_state.bench_players.copy()
    if player_in not in bench or player_out not in attack + defense:
        st.error("Invalid substitution: check the current lineup and bench.")
        return
    if st.session_state.player_genders[player_out] != st.session_state.player_genders[player_in]:
        st.error("A male player must be replaced by a male player, and a female player by a female player.")
        return
    if player_out in attack:
        attack[attack.index(player_out)] = player_in
    else:
        defense[defense.index(player_out)] = player_in
    valid, message = validate_lineup(
        attack, defense, st.session_state.player_genders, st.session_state.competition_type
    )
    if not valid:
        st.error(message)
        return
    add_match_event({"type": "substitution", "player_out": player_out, "player_in": player_in})


def show_new_match():
    st.subheader("New match")
    team = st.text_input("Team")
    opponent = st.text_input("Opponent")
    match_date = st.date_input("Date")
    competition_type = st.selectbox("Competition type", COMPETITION_TYPES)
    players_input = st.text_area("Players (one per line)", placeholder="Enter player names, one per line.")
    players = list(dict.fromkeys(name.strip() for name in players_input.splitlines() if name.strip()))
    st.subheader("Player information")
    genders = {}
    for player in players:
        genders[player] = st.selectbox(
            f"Gender - {player}", ["Male", "Female"],
            index=1 if competition_type == "Women" else 0,
            key=f"gender_{player}",
        )
    st.number_input("Match duration (minutes)", min_value=10, max_value=120, value=60, step=5, key="match_duration_minutes")
    st.subheader("Starting lineup")
    attack = defense = bench = []
    if len(players) >= 8:
        attack = st.multiselect("Attack (select 4 players)", players, max_selections=4)
        available = [p for p in players if p not in attack]
        defense = st.multiselect("Defense (select 4 players)", available, max_selections=4)
        bench = [p for p in players if p not in attack and p not in defense]
        if len(attack) == 4:
            male, female = count_genders(attack, genders)
            st.caption(f"Attack: {male} male, {female} female")
        if len(defense) == 4:
            male, female = count_genders(defense, genders)
            st.caption(f"Defense: {male} male, {female} female")
        st.write("**Bench:** " + (", ".join(bench) if bench else "No substitutes"))
    else:
        st.info("Enter at least 8 players to select a starting lineup.")
    if st.button("Start match"):
        if not team.strip() or not opponent.strip():
            st.warning("Please enter both team names.")
            return
        if len(players) < 8:
            st.warning("Please enter at least 8 players.")
            return
        valid, message = validate_lineup(attack, defense, genders, competition_type)
        if not valid:
            st.warning(message)
            return
        st.session_state.team = team.strip()
        st.session_state.opponent = opponent.strip()
        st.session_state.match_date = match_date
        st.session_state.competition_type = competition_type
        st.session_state.players = players.copy()
        st.session_state.player_genders = genders.copy()
        st.session_state.starting_attack = attack.copy()
        st.session_state.starting_defense = defense.copy()
        st.session_state.starting_bench = bench.copy()
        st.session_state.match_events = []
        st.session_state.clock_elapsed = 0.0
        st.session_state.clock_started_at = None
        st.session_state.clock_running = False
        st.session_state.match_finished = False
        st.session_state.match_id = None
        rebuild_match_state()
        st.session_state.shot_x = None
        st.session_state.shot_y = None
        st.session_state.field_key_number += 1
        st.session_state.match_started = True
        st.rerun()


def show_score_and_lineup():
    st.success(f"{st.session_state.team} vs {st.session_state.opponent}")
    st.write(f"Date: {st.session_state.match_date}")
    left, middle, right = st.columns(3)
    left.metric(st.session_state.team, st.session_state.our_score)
    middle.metric(st.session_state.opponent, st.session_state.opponent_score)
    right.metric("Goals until zone switch", 2 - st.session_state.goals_since_switch)
    st.caption(f"Automatic zone switches: {st.session_state.zone_switches}")
    with st.expander("Current lineup", expanded=True):
        cols = st.columns(3)
        for col, label, players in zip(
            cols, ["Attack", "Defense", "Bench"],
            [st.session_state.attack_players, st.session_state.defense_players, st.session_state.bench_players],
        ):
            with col:
                st.write(f"**{label}**")
                if players:
                    for player in players:
                        st.write(f"- {player}")
                else:
                    st.write("No substitutes")


def show_shot_registration():
    st.subheader("Register action")
    st.caption("Click on the field to register a shot, rebound, interception, turnover, foul or start of an attack.")
    coordinates = streamlit_image_coordinates(
        create_korfball_field(), width=FIELD_IMAGE_SIZE,
        key=f"korfball_field_{st.session_state.field_key_number}",
    )
    if coordinates is not None:
        x, y = coordinates["x"], coordinates["y"]
        margin = FIELD_MARGIN
        max_pixel = FIELD_IMAGE_SIZE - margin
        if margin <= x <= max_pixel and margin <= y <= max_pixel:
            field_size = FIELD_IMAGE_SIZE - 2 * margin
            new_x = (x - margin) / field_size * FIELD_METRES
            new_y = (y - margin) / field_size * FIELD_METRES
            if new_x != st.session_state.shot_x or new_y != st.session_state.shot_y:
                st.session_state.shot_x = new_x
                st.session_state.shot_y = new_y
                st.rerun()
    if st.session_state.shot_x is not None and st.session_state.shot_y is not None:
        register_field_event_dialog()
    else:
        st.info("Click on the field to select the event location.")


def show_substitutions():
    st.subheader("Substitutions")
    used = count_substitutions()
    limit = get_substitution_limit()
    if limit is None:
        st.metric("Substitutions used", used, help="No substitution limit configured for this match type.")
    else:
        col1, col2 = st.columns(2)
        col1.metric("Substitutions used", f"{used} / {limit}")
        col2.metric("Substitutions remaining", max(0, limit - used))
        st.progress(min(used / limit, 1.0))
        if used >= limit:
            st.warning("Maximum number of substitutions reached.")
            return
    if not st.session_state.bench_players:
        st.info("No players available on the bench.")
        return
    field_players = st.session_state.attack_players + st.session_state.defense_players
    player_out = st.selectbox("Player out", field_players, key="sub_player_out")
    player_gender = st.session_state.player_genders[player_out]
    eligible = [
        p for p in st.session_state.bench_players
        if st.session_state.player_genders[p] == player_gender
    ]
    player_in = st.selectbox(
        "Player in", eligible, index=0 if eligible else None,
        placeholder="No eligible substitutes", key=f"sub_player_in_{player_out}",
    )
    if st.button("Confirm substitution", disabled=player_in is None):
        substitute_player(player_out, player_in)


def describe_event(event):
    stamp = format_clock(event.get("time_seconds", 0))
    if event["type"] == "action":
        return f"{stamp} | {event['team']}: {event['action']} - {event['player']}"
    if event["type"] == "substitution":
        return f"{stamp} | Substitution: {event['player_out']} out, {event['player_in']} in"
    if event["team"] == "Our team":
        return f"{stamp} | Our team: {event['player']} - {event['shot_type']} - {event['result']}"
    return f"{stamp} | Opponent: {event['shot_type']} - {event['result']} - Defender: {event['player']}"


def show_event_history():
    st.subheader("Match events")
    if not st.session_state.match_events:
        st.info("No match events registered yet.")
        return
    for index, event in enumerate(reversed(st.session_state.match_events), start=1):
        st.write(f"{index}. {describe_event(event)}")
    if st.button("↩️ Undo last event"):
        undo_last_event()


def show_statistics():
    st.subheader("Match statistics")
    own_shots = [a for a in st.session_state.actions if a["team"] == "Our team"]
    opponent_shots = [a for a in st.session_state.actions if a["team"] == "Opponent"]
    total_shots = len(own_shots)
    total_goals = sum(a["result"] == "Goal" for a in own_shots)
    pct = total_goals / total_shots * 100 if total_shots else 0
    cols = st.columns(4)
    cols[0].metric("Our shots", total_shots)
    cols[1].metric("Our goals", total_goals)
    cols[2].metric("Shot %", f"{pct:.1f}%")
    cols[3].metric("Opponent shots", len(opponent_shots))
    st.subheader("Player statistics")
    player_stats = []
    for player in st.session_state.players:
        shots = [a for a in own_shots if a["player"] == player]
        goals = sum(a["result"] == "Goal" for a in shots)
        player_stats.append({
            "Player": player, "Shots": len(shots), "Goals": goals,
            "Shot %": round(goals / len(shots) * 100, 1) if shots else 0,
        })
    st.dataframe(pd.DataFrame(player_stats), hide_index=True, width="stretch")
    st.subheader("Defensive statistics")
    defense_stats = []
    for player in st.session_state.players:
        defended = [a for a in opponent_shots if a["player"] == player]
        conceded = sum(a["result"] == "Goal" for a in defended)
        defense_stats.append({
            "Player": player, "Opponent shots": len(defended),
            "Goals conceded while involved": conceded,
        })
    st.dataframe(pd.DataFrame(defense_stats), hide_index=True, width="stretch")
    st.caption("A conceded goal does not necessarily mean the selected defender was at fault.")
    st.subheader("Shot type statistics per player")
    analysis_player = st.selectbox("Select player for analysis", st.session_state.players, key="analysis_player")
    player_actions = [a for a in own_shots if a["player"] == analysis_player]
    shot_type_stats = []
    for shot_type in SHOT_TYPES:
        shots = [a for a in player_actions if a["shot_type"] == shot_type]
        if shots:
            goals = sum(a["result"] == "Goal" for a in shots)
            shot_type_stats.append({
                "Shot type": shot_type, "Shots": len(shots), "Goals": goals,
                "Shot %": round(goals / len(shots) * 100, 1),
            })
    if shot_type_stats:
        st.dataframe(pd.DataFrame(shot_type_stats), hide_index=True, width="stretch")
    else:
        st.info("No shots registered for this player yet.")
    st.subheader("Detailed player statistics")
    st.dataframe(detailed_stats(), hide_index=True, width="stretch")
    st.subheader("Playing time and lineup changes")
    times, timeline = playing_time_and_lineups()
    st.dataframe(times, hide_index=True, width="stretch")
    if not timeline.empty:
        st.dataframe(timeline, hide_index=True, width="stretch")
    st.caption("Playing time is calculated from the match clock and recorded events; start the clock before registering actions.")
    attacks = [e for e in st.session_state.match_events if e["type"] == "action" and e["action"] == "Attack started" and e["team"] == "Our team"]
    if attacks:
        st.metric("Goals per recorded attack", f"{st.session_state.our_score / len(attacks):.2f}")
        st.caption("Only meaningful when every attack is registered consistently.")


def show_active_match():
    show_clock()
    show_score_and_lineup()
    st.divider()
    show_shot_registration()
    st.divider()
    show_substitutions()
    st.divider()
    show_other_actions()
    st.divider()
    show_event_history()
    st.divider()
    show_statistics()
    st.divider()
    show_shot_map()
    st.divider()
    show_exports()
    st.divider()
    show_save_controls()



# ==================================================
# MATCH CLOCK AND TIME
# ==================================================

def clock_seconds():
    seconds = float(st.session_state.clock_elapsed)
    if st.session_state.clock_running and st.session_state.clock_started_at:
        start = datetime.fromisoformat(st.session_state.clock_started_at)
        seconds += max(0, (datetime.now() - start).total_seconds())
    return seconds


def format_clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def pause_clock():
    st.session_state.clock_elapsed = clock_seconds()
    st.session_state.clock_started_at = None
    st.session_state.clock_running = False


@st.fragment(run_every="1s")
def live_clock_display():
    st.metric("Elapsed time", format_clock(clock_seconds()))


def show_clock():
    st.subheader("Match clock")
    live_clock_display()
    st.caption("The clock display refreshes automatically while running. Event timestamps use the actual elapsed time.")
    a, b, c = st.columns(3)
    with a:
        if st.button("Start / Resume", disabled=st.session_state.clock_running or st.session_state.match_finished):
            st.session_state.clock_started_at = datetime.now().isoformat()
            st.session_state.clock_running = True
            st.rerun()
    with b:
        if st.button("Pause", disabled=not st.session_state.clock_running):
            pause_clock()
            st.rerun()
    with c:
        st.caption("Live display: 1-second updates")
    if not st.session_state.match_events:
        if st.button("Reset clock", disabled=st.session_state.clock_running):
            st.session_state.clock_elapsed = 0.0
            st.rerun()


# ==================================================
# ADDITIONAL MATCH EVENTS
# ==================================================

OTHER_ACTIONS = ["Rebound", "Interception", "Turnover", "Foul", "Attack started"]


def show_other_actions():
    st.caption("Other match actions are registered by clicking their location on the field above.")


# ==================================================
# PLAYING TIME / LINEUP TIMELINE
# ==================================================

def playing_time_and_lineups():
    elapsed = clock_seconds()
    attack = st.session_state.starting_attack.copy()
    defense = st.session_state.starting_defense.copy()
    bench = st.session_state.starting_bench.copy()
    totals = {p: {"Attack (min)": 0.0, "Defense (min)": 0.0, "Bench (min)": 0.0} for p in st.session_state.players}
    timeline = []
    previous = 0.0
    goals = 0
    def accumulate(end):
        duration = max(0.0, end - previous) / 60.0
        for p in attack:
            totals[p]["Attack (min)"] += duration
        for p in defense:
            totals[p]["Defense (min)"] += duration
        for p in bench:
            totals[p]["Bench (min)"] += duration
    for event in st.session_state.match_events:
        timestamp = max(previous, min(elapsed, float(event.get("time_seconds", 0))))
        accumulate(timestamp)
        previous = timestamp
        if event["type"] == "substitution":
            out, incoming = event["player_out"], event["player_in"]
            if out in attack:
                attack[attack.index(out)] = incoming
            else:
                defense[defense.index(out)] = incoming
            bench.remove(incoming)
            bench.append(out)
            timeline.append({"Time": format_clock(timestamp), "Change": "Substitution", "Attack": ", ".join(attack), "Defense": ", ".join(defense)})
        elif event["type"] == "shot" and event["result"] == "Goal":
            goals += 1
            if goals % 2 == 0:
                attack, defense = defense, attack
                timeline.append({"Time": format_clock(timestamp), "Change": "Zone switch", "Attack": ", ".join(attack), "Defense": ", ".join(defense)})
    accumulate(elapsed)
    rows = []
    for p, d in totals.items():
        rows.append({"Player": p, **{k: round(v, 1) for k, v in d.items()}, "On field (min)": round(d["Attack (min)"] + d["Defense (min)"], 1)})
    return pd.DataFrame(rows), pd.DataFrame(timeline)


def detailed_stats():
    own = [e for e in st.session_state.match_events if e["type"] == "shot" and e["team"] == "Our team"]
    other = [e for e in st.session_state.match_events if e["type"] == "action"]
    rows = []
    for p in st.session_state.players:
        shots = [e for e in own if e["player"] == p]
        goals = sum(e["result"] == "Goal" for e in shots)
        d = {a: sum(e["player"] == p and e["action"] == a for e in other) for a in OTHER_ACTIONS}
        rows.append({"Player": p, "Shots": len(shots), "Goals": goals,
                     "Shot %": round(100 * goals / len(shots), 1) if shots else 0,
                     "Average distance (m)": round(sum(e["distance"] for e in shots) / len(shots), 2) if shots else 0,
                     **d})
    return pd.DataFrame(rows)


# ==================================================
# SHOT MAP / HEATMAP
# ==================================================

EVENT_COLORS = {
    "Goal": "#179B57",
    "Miss": "#D83434",
    "Rebound": "#2374C6",
    "Interception": "#8546C7",
    "Turnover": "#E68A24",
    "Foul": "#E0B800",
    "Attack started": "#727B84",
}


def event_label(event):
    return event["result"] if event["type"] == "shot" else event.get("action", "Other")


def map_background():
    image = Image.new("RGB", (FIELD_IMAGE_SIZE, FIELD_IMAGE_SIZE), "white")
    draw = ImageDraw.Draw(image)
    margin = FIELD_MARGIN
    scale = (FIELD_IMAGE_SIZE - 2 * margin) / FIELD_METRES
    draw.rectangle((margin, margin, FIELD_IMAGE_SIZE-margin, FIELD_IMAGE_SIZE-margin), outline="black", width=3)
    px, py = margin + POST_X * scale, margin + POST_Y * scale
    draw_distance_rings(draw, px, py, scale)
    draw.ellipse((px-6, py-6, px+6, py+6), fill="black")
    return image


def map_point(event):
    scale = (FIELD_IMAGE_SIZE - 2 * FIELD_MARGIN) / FIELD_METRES
    return (round(FIELD_MARGIN + event["x"] * scale), round(FIELD_MARGIN + event["y"] * scale))


def event_map_image(events, heatmap=False):
    image = map_background()
    if heatmap and events:
        density = Image.new("L", image.size, 0)
        painter = ImageDraw.Draw(density)
        for event in events:
            x, y = map_point(event)
            painter.ellipse((x-14, y-14, x+14, y+14), fill=95)
        density = density.filter(ImageFilter.GaussianBlur(24))
        maximum = max(1, density.getextrema()[1])
        density = density.point(lambda v: min(255, int(v * 255 / maximum)))
        colored = ImageOps.colorize(density, black="#FFF7D1", white="#C62828")
        alpha = density.point(lambda v: min(205, int(v * 0.8)))
        image.paste(colored, (0, 0), alpha)
        overlay = ImageDraw.Draw(image)
        px, py = map_point({"x": POST_X, "y": POST_Y})
        draw_distance_rings(overlay, px, py, (FIELD_IMAGE_SIZE - 2 * FIELD_MARGIN) / FIELD_METRES)
        overlay.ellipse((px-6, py-6, px+6, py+6), fill="black")
    elif not heatmap:
        painter = ImageDraw.Draw(image)
        for event in events:
            x, y = map_point(event)
            painter.ellipse((x-6, y-6, x+6, y+6), fill=EVENT_COLORS.get(event_label(event), "#444444"), outline="white", width=1)
    return image


def shot_map_image(shots, heatmap=False):
    return event_map_image(shots, heatmap=heatmap)


def filtered_shots():
    st.subheader("Event map and heatmap")
    events = [e for e in st.session_state.match_events if e["type"] in ("shot", "action") and "x" in e and "y" in e]
    if not events:
        st.info("Register shots or other actions to see the event map.")
        return [], None

    view = st.radio("Map view", ["Colored event map", "Density heatmap"], horizontal=True)
    team = st.selectbox("Team", ["All teams", "Our team", "Opponent"], key="event_map_team")
    selected = [e for e in events if team == "All teams" or e["team"] == team]
    categories = [label for label in EVENT_COLORS if any(event_label(e) == label for e in selected)]
    kinds = st.multiselect("Actions to display", categories, default=categories, key="event_map_categories")
    selected = [e for e in selected if event_label(e) in kinds]
    players = sorted({e["player"] for e in selected})
    player = st.selectbox("Player / defender", ["All"] + players, key="event_map_player")
    period = st.selectbox("Time period", ["All", "First half", "Second half"], key="event_map_period")
    shot_types = [e["shot_type"] for e in selected if e["type"] == "shot"]
    shot_type = st.selectbox("Shot type", ["All"] + sorted(set(shot_types)), key="event_map_shot_type")
    half_seconds = float(st.session_state.get("match_duration_minutes", 60)) * 30
    selected = [e for e in selected
                if (player == "All" or e["player"] == player)
                and (shot_type == "All" or e["type"] != "shot" or e["shot_type"] == shot_type)
                and (period == "All" or (e.get("time_seconds", 0) < half_seconds if period == "First half" else e.get("time_seconds", 0) >= half_seconds))]

    is_heatmap = view == "Density heatmap"
    image = event_map_image(selected, heatmap=is_heatmap)
    st.image(image, caption=f"{len(selected)} events · " + ("Higher intensity = more events" if is_heatmap else "Colors indicate event type"), width=550)
    if is_heatmap:
        st.caption("The heatmap shows the concentration of the selected actions; its colors indicate density, not action type. Select one action to analyze its hotspots.")
    else:
        st.markdown("**Color legend:** " + " · ".join(
            f'<span style="color:{color}">●</span> {name}'
            for name, color in EVENT_COLORS.items() if name in kinds
        ), unsafe_allow_html=True)
    return selected, image


def show_shot_map():
    selected, image = filtered_shots()
    if image is not None:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        st.download_button("Download event map (PNG)", buffer.getvalue(), file_name="korfball_event_map.png", mime="image/png")


# ==================================================
# PERSISTENT MATCH STORAGE (SQLite)
# ==================================================

DB_PATH = Path(__file__).resolve().parent.parent / "korfball_matches.sqlite3"


def db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, saved_at TEXT NOT NULL, team TEXT NOT NULL, opponent TEXT NOT NULL, match_date TEXT NOT NULL, payload TEXT NOT NULL)")
    conn.commit()
    return conn


def snapshot():
    return {
        "team": st.session_state.team, "opponent": st.session_state.opponent,
        "match_date": str(st.session_state.match_date),
        "competition_type": st.session_state.competition_type,
        "players": st.session_state.players,
        "player_genders": st.session_state.player_genders,
        "starting_attack": st.session_state.starting_attack,
        "starting_defense": st.session_state.starting_defense,
        "starting_bench": st.session_state.starting_bench,
        "match_events": st.session_state.match_events,
        "clock_elapsed": clock_seconds(),
        "match_finished": st.session_state.match_finished,
        "match_duration_minutes": st.session_state.get("match_duration_minutes", 60),
    }


def save_match():
    pause_clock()
    data = snapshot()
    with db_connection() as conn:
        if st.session_state.match_id is None:
            cursor = conn.execute("INSERT INTO matches (saved_at, team, opponent, match_date, payload) VALUES (?, ?, ?, ?, ?)",
                                  (datetime.now().isoformat(), data["team"], data["opponent"], data["match_date"], json.dumps(data)))
            st.session_state.match_id = cursor.lastrowid
        else:
            conn.execute("UPDATE matches SET saved_at=?, team=?, opponent=?, match_date=?, payload=? WHERE id=?",
                         (datetime.now().isoformat(), data["team"], data["opponent"], data["match_date"], json.dumps(data), st.session_state.match_id))
    st.success("Match saved. The clock has been paused.")


def load_match(match_id):
    with db_connection() as conn:
        row = conn.execute("SELECT payload FROM matches WHERE id=?", (int(match_id),)).fetchone()
    if not row:
        st.error("Match not found.")
        return
    data = json.loads(row[0])
    for key in ("team", "opponent", "competition_type", "players", "player_genders", "starting_attack", "starting_defense", "starting_bench", "match_events", "match_finished", "match_duration_minutes"):
        if key in data:
            st.session_state[key] = data[key]
    st.session_state.match_date = date.fromisoformat(data["match_date"])
    st.session_state.clock_elapsed = float(data.get("clock_elapsed", 0))
    st.session_state.clock_started_at = None
    st.session_state.clock_running = False
    st.session_state.match_id = int(match_id)
    st.session_state.match_started = True
    st.session_state.shot_x = None
    st.session_state.shot_y = None
    st.session_state.field_key_number += 1
    rebuild_match_state()
    st.rerun()


def show_save_controls():
    st.subheader("Save / finish match")
    st.caption("Saving pauses the clock. You can reopen saved matches in View analysis.")
    if st.button("Save match", type="primary"):
        try:
            save_match()
        except (sqlite3.Error, OSError) as exc:
            st.error(f"Could not save match: {exc}")
    if not st.session_state.match_finished:
        if st.button("Finish match"):
            pause_clock()
            st.session_state.match_finished = True
            try:
                save_match()
            except (sqlite3.Error, OSError) as exc:
                st.error(f"Could not save match: {exc}")
            else:
                st.rerun()
    else:
        st.info("Match finished. You can review the statistics or reopen it for editing.")
        if st.button("Resume editing"):
            st.session_state.match_finished = False
            st.rerun()
    if st.button("Leave match (without automatic saving)"):
        pause_clock()
        st.session_state.match_started = False
        st.rerun()

## Save as Excel, PDF, or CSV report. And upload as Excel or CSV.

def event_rows():
    rows = []
    for e in st.session_state.match_events:
        rows.append({
            "Time": format_clock(e.get("time_seconds", 0)), 
            "Event": describe_event(e),
            "Type": e["type"], 
            "Team": e.get("team", ""), 
            "Player": e.get("player", ""),
            "Result": e.get("result", ""), 
            "Shot type": e.get("shot_type", ""),
            "X (m)": e.get("x", ""), 
            "Y (m)": e.get("y", ""), 
            "Distance (m)": e.get("distance", "")
        })
    return pd.DataFrame(rows)


def excel_report():
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    
    score_str = f"{st.session_state.our_score} - {st.session_state.opponent_score}"
    
    info = [
        ("Team", st.session_state.team), 
        ("Opponent", st.session_state.opponent),
        ("Date", str(st.session_state.match_date)), 
        ("Competition", st.session_state.competition_type),
        ("Score", score_str),
        ("Duration", format_clock(clock_seconds())), 
        ("Substitutions", count_substitutions())
    ]
    for row in info:
        ws.append(row)
        
    tables = {
        "Player statistics": detailed_stats(), 
        "Playing time": playing_time_and_lineups()[0],
        "Lineup changes": playing_time_and_lineups()[1], 
        "Events": event_rows()
    }
    
    for title, frame in tables.items():
        sheet = wb.create_sheet(title)
        if frame.empty and len(frame.columns) == 0:
            sheet.append(["No data"])
        else:
            sheet.append(list(frame.columns))
            for values in frame.itertuples(index=False, name=None):
                sheet.append(list(values))
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="23415D")
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(45, max(14, max(len(str(c.value or "")) for c in column) + 2))
            
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def pdf_report():
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, rightMargin=35, leftMargin=35, topMargin=35, bottomMargin=35)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Korfball match report", styles["Title"]), Spacer(1, 12)]
    
    score_str = f"{st.session_state.our_score} - {st.session_state.opponent_score}"
    
    details = [
        f"{st.session_state.team} vs {st.session_state.opponent}",
        f"Date: {st.session_state.match_date}",
        f"Score: {score_str}",
        f"Match time: {format_clock(clock_seconds())}",
        f"Substitutions: {count_substitutions()}"
    ]
    for detail in details:
        elements.append(Paragraph(escape(detail), styles["Normal"]))
        
    elements.append(Spacer(1, 16))
    elements.append(Paragraph("Player statistics", styles["Heading2"]))
    frame = detailed_stats()[["Player", "Shots", "Goals", "Shot %", "Average distance (m)"]]
    data = [list(frame.columns)] + [list(row) for row in frame.itertuples(index=False, name=None)]
    data = [[str(x) for x in row] for row in data]
    table = Table(data, repeatRows=1, colWidths=[150, 65, 65, 70, 120])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#23415D")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6)
    ]))
    elements.append(table)
    elements.append(Spacer(1, 16))
    elements.append(Paragraph("Our shot map", styles["Heading2"]))
    own = [e for e in st.session_state.match_events if e["type"] == "shot" and e["team"] == "Our team"]
    image = shot_map_image(own)
    png = io.BytesIO()
    image.save(png, format="PNG")
    png.seek(0)
    elements.append(PDFImage(png, width=300, height=300))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Match events", styles["Heading2"]))
    for event in st.session_state.match_events:
        elements.append(Paragraph(escape(describe_event(event)), styles["Normal"]))
    doc.build(elements)
    return out.getvalue()


def show_exports():
    st.subheader("Export Match Data & Reports")
    
    df_events = event_rows()
    csv_data = df_events.to_csv(index=False).encode('utf-8') if not df_events.empty else b""
    excel_data = excel_report()
    pdf_data = pdf_report()

    # Maak een veilige bestandsnaam op basis van team, tegenstander en datum
    safe_team = "".join(c for c in st.session_state.team if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    safe_opp = "".join(c for c in st.session_state.opponent if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    match_name = f"{safe_team}_vs_{safe_opp}_{st.session_state.match_date}" if safe_team and safe_opp else "korfball_match"

    # Genereer een ZIP-bestand met de 3 bestanden erin
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr(f"{match_name}_data.csv", csv_data)
        zip_file.writestr(f"{match_name}_report.xlsx", excel_data)
        zip_file.writestr(f"{match_name}_report.pdf", pdf_data)
    zip_buffer.seek(0)

    st.download_button(
        label="📦 Download Alle Bestanden (PDF, Excel & CSV)",
        data=zip_buffer,
        file_name=f"{match_name}_export.zip",
        mime="application/zip",
        use_container_width=True
    )


def show_saved_matches():
    st.subheader("Load saved korfball matches")
    st.caption("Upload a previously exported CSV or Excel match file to continue your analysis.")
    
    uploaded_file = st.file_uploader("Choose match file", type=["csv", "xlsx"])
    
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith('.csv'):
                st.session_state.events_df = pd.read_csv(uploaded_file)
            else:
                st.session_state.events_df = pd.read_excel(uploaded_file)
                
            st.success("Match data successfully loaded into memory!")
        except Exception as e:
            st.error(f"Could not read the match file: {e}")
            
    st.caption("Loading a file replaces the current active session data.")


def show():
    initialize_state()
    st.header("Korfball")
    mode = st.radio("Select mode:", ["Register match", "View analysis"], horizontal=True)
    st.divider()
    if mode == "Register match":
        if st.session_state.match_started:
            show_active_match()
        else:
            show_new_match()
    else:
        show_saved_matches()
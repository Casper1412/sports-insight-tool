
import math

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates


# ==================================================
# CONSTANTS
# ==================================================

FIELD_IMAGE_SIZE = 700
FIELD_MARGIN = 30
FIELD_METRES = 20

POST_X = 10.0
POST_Y = 6.67

SHOT_TYPES = [
    "Long shot",
    "Short shot",
    "Running in shot",
    "Goalzone shot",
    "Free shot",
    "Penalty",
]

COMPETITION_TYPES = [
    "Mixed - Standard",
    "Mixed - Recreational",
    "Women",
]


# ==================================================
# SESSION STATE
# ==================================================

def initialize_state():

    defaults = {
        "match_started": False,
        "team": "",
        "opponent": "",
        "match_date": None,
        "competition_type": "Mixed - Standard",
        "players": [],
        "player_genders": {},
        "starting_attack": [],
        "starting_defense": [],
        "starting_bench": [],
        "attack_players": [],
        "defense_players": [],
        "bench_players": [],
        "actions": [],
        "match_events": [],
        "our_score": 0,
        "opponent_score": 0,
        "goals_since_switch": 0,
        "zone_switches": 0,
        "shot_x": None,
        "shot_y": None,
        "field_key_number": 0,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ==================================================
# LINEUP VALIDATION
# ==================================================

def count_genders(players, player_genders):

    male = sum(
        player_genders[player] == "Male"
        for player in players
    )

    female = sum(
        player_genders[player] == "Female"
        for player in players
    )

    return male, female


def validate_lineup(
    attack,
    defense,
    player_genders,
    competition_type,
):

    if len(attack) != 4 or len(defense) != 4:
        return False, "Each zone must contain 4 players."

    if len(set(attack + defense)) != 8:
        return False, "A player cannot occupy two positions."

    attack_male, _ = count_genders(
        attack, player_genders
    )

    defense_male, _ = count_genders(
        defense, player_genders
    )

    if competition_type == "Mixed - Standard":

        if attack_male != 2 or defense_male != 2:
            return (
                False,
                "Each zone requires 2 male and 2 female players.",
            )

    elif competition_type == "Mixed - Recreational":

        # Simplified distribution check.
        # Additional KNKV exceptions are not modelled yet.
        if abs(attack_male - defense_male) > 1:
            return (
                False,
                "Distribute male and female players "
                "as evenly as possible between the zones.",
            )

    elif competition_type == "Women":

        if attack_male != 0 or defense_male != 0:
            return (
                False,
                "All players on the field must be female.",
            )

    return True, "Valid lineup"


# ==================================================
# REBUILD MATCH STATE FROM EVENTS
# ==================================================

def rebuild_match_state():

    attack = st.session_state.starting_attack.copy()
    defense = st.session_state.starting_defense.copy()
    bench = st.session_state.starting_bench.copy()

    our_score = 0
    opponent_score = 0
    goals_since_switch = 0
    zone_switches = 0
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

            player_out = event["player_out"]
            player_in = event["player_in"]

            if player_out in attack:
                index = attack.index(player_out)
                attack[index] = player_in

            elif player_out in defense:
                index = defense.index(player_out)
                defense[index] = player_in

            bench.remove(player_in)
            bench.append(player_out)

    st.session_state.attack_players = attack
    st.session_state.defense_players = defense
    st.session_state.bench_players = bench

    st.session_state.our_score = our_score
    st.session_state.opponent_score = opponent_score
    st.session_state.goals_since_switch = goals_since_switch
    st.session_state.zone_switches = zone_switches
    st.session_state.actions = actions


def add_match_event(event):

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


# ==================================================
# CREATE KORFBALL FIELD
# ==================================================

def create_korfball_field():

    size = FIELD_IMAGE_SIZE
    margin = FIELD_MARGIN

    pixels_per_meter = (
        size - 2 * margin
    ) / FIELD_METRES

    image = Image.new(
        "RGB",
        (size, size),
        "white",
    )

    draw = ImageDraw.Draw(image)

    draw.rectangle(
        (margin, margin, size - margin, size - margin),
        outline="black",
        width=3,
    )

    post_px = margin + POST_X * pixels_per_meter
    post_py = margin + POST_Y * pixels_per_meter

    for radius in (2, 4, 6):

        radius_px = radius * pixels_per_meter

        draw.ellipse(
            (
                post_px - radius_px,
                post_py - radius_px,
                post_px + radius_px,
                post_py + radius_px,
            ),
            outline="#9B9B9B",
            width=2,
        )

    draw.ellipse(
        (
            post_px - 7,
            post_py - 7,
            post_px + 7,
            post_py + 7,
        ),
        fill="black",
    )

    if (
        st.session_state.shot_x is not None
        and st.session_state.shot_y is not None
    ):

        shot_px = (
            margin
            + st.session_state.shot_x * pixels_per_meter
        )

        shot_py = (
            margin
            + st.session_state.shot_y * pixels_per_meter
        )

        marker_size = 9

        draw.line(
            (
                shot_px - marker_size,
                shot_py - marker_size,
                shot_px + marker_size,
                shot_py + marker_size,
            ),
            fill="#D62828",
            width=4,
        )

        draw.line(
            (
                shot_px - marker_size,
                shot_py + marker_size,
                shot_px + marker_size,
                shot_py - marker_size,
            ),
            fill="#D62828",
            width=4,
        )

    return image


# ==================================================
# SHOT REGISTRATION
# ==================================================

def register_shot(team, player, shot_type, result):

    if (
        st.session_state.shot_x is None
        or st.session_state.shot_y is None
    ):
        st.warning("Select a shot location first.")
        return

    if team == "Our team":

        if player not in st.session_state.attack_players:
            st.error("This player is not currently attacking.")
            return

    else:

        if player not in st.session_state.defense_players:
            st.error("This player is not currently defending.")
            return

    x = st.session_state.shot_x
    y = st.session_state.shot_y

    distance = math.hypot(
        x - POST_X,
        y - POST_Y,
    )

    event = {
        "type": "shot",
        "team": team,
        "player": player,
        "shot_type": shot_type,
        "result": result,
        "x": round(x, 3),
        "y": round(y, 3),
        "distance": round(distance, 3),
    }

    add_match_event(event)


@st.dialog("Register shot")
def register_shot_dialog():

    distance = math.hypot(
        st.session_state.shot_x - POST_X,
        st.session_state.shot_y - POST_Y,
    )

    st.write(f"Shot distance: {distance:.1f} m")

    team = st.radio(
        "Shot by",
        ["Our team", "Opponent"],
        horizontal=True,
    )

    if team == "Our team":

        players = st.session_state.attack_players
        player_label = "Attacking player"

    else:

        players = st.session_state.defense_players
        player_label = "Defending player"

        st.caption(
            "Select the defender involved in the action. "
            "This does not automatically assign responsibility "
            "for a conceded goal."
        )

    selected_player = st.selectbox(
        player_label,
        players,
    )

    selected_shot_type = st.selectbox(
        "Shot type",
        SHOT_TYPES,
    )

    col1, col2 = st.columns(2)

    with col1:

        if st.button("Goal", width="stretch"):

            register_shot(
                team,
                selected_player,
                selected_shot_type,
                "Goal",
            )

    with col2:

        if st.button("Miss", width="stretch"):

            register_shot(
                team,
                selected_player,
                selected_shot_type,
                "Miss",
            )

    if st.button("Cancel"):

        st.session_state.shot_x = None
        st.session_state.shot_y = None
        st.session_state.field_key_number += 1

        st.rerun()


# ==================================================
# SUBSTITUTIONS
# ==================================================

def substitute_player(player_out, player_in):
    gender_out = st.session_state.player_genders[player_out]
    gender_in = st.session_state.player_genders[player_in]

    if gender_out != gender_in:
        st.error(
            "A male player must be replaced by a male player, "
            "and a female player by a female player."
        )
        return

    attack = st.session_state.attack_players.copy()
    defense = st.session_state.defense_players.copy()
    bench = st.session_state.bench_players.copy()

    if player_in not in bench:
        st.error("Incoming player is not on the bench.")
        return

    if player_out in attack:

        index = attack.index(player_out)
        attack[index] = player_in

    elif player_out in defense:

        index = defense.index(player_out)
        defense[index] = player_in

    else:
        st.error("Outgoing player is not on the field.")
        return

    valid, message = validate_lineup(
        attack,
        defense,
        st.session_state.player_genders,
        st.session_state.competition_type,
    )

    if not valid:
        st.error(message)
        return

    event = {
        "type": "substitution",
        "player_out": player_out,
        "player_in": player_in,
    }

    add_match_event(event)


# ==================================================
# NEW MATCH
# ==================================================

def show_new_match():

    st.subheader("New match")

    team = st.text_input("Team")
    opponent = st.text_input("Opponent")
    match_date = st.date_input("Date")

    competition_type = st.selectbox(
        "Competition type",
        COMPETITION_TYPES,
    )

    players_input = st.text_area(
        "Players (one per line)",
        placeholder="Enter player names, one per line.",
    )

    players = [
        name.strip()
        for name in players_input.splitlines()
        if name.strip()
    ]

    players = list(dict.fromkeys(players))

    st.subheader("Player information")

    player_genders = {}

    for player in players:

        player_genders[player] = st.selectbox(
            f"Gender - {player}",
            ["Male", "Female"],
            index=(
                1 if competition_type == "Women" else 0
            ),
            key=f"gender_{player}",
        )

    st.subheader("Starting lineup")

    attack_players = []
    defense_players = []
    bench_players = []

    if len(players) >= 8:

        attack_players = st.multiselect(
            "Attack (select 4 players)",
            players,
            max_selections=4,
        )

        available_defenders = [
            player
            for player in players
            if player not in attack_players
        ]

        defense_players = st.multiselect(
            "Defense (select 4 players)",
            available_defenders,
            max_selections=4,
        )

        bench_players = [
            player
            for player in players
            if player not in attack_players
            and player not in defense_players
        ]

        if len(attack_players) == 4:

            male, female = count_genders(
                attack_players,
                player_genders,
            )

            st.caption(
                f"Attack: {male} male, {female} female"
            )

        if len(defense_players) == 4:

            male, female = count_genders(
                defense_players,
                player_genders,
            )

            st.caption(
                f"Defense: {male} male, {female} female"
            )

        st.write("**Bench:**")

        if bench_players:
            st.write(", ".join(bench_players))
        else:
            st.write("No substitutes")

    else:

        st.info(
            "Enter at least 8 players to select a starting lineup."
        )

    if st.button("Start match"):

        if not team.strip():
            st.warning("Please enter the team name.")
            return

        if not opponent.strip():
            st.warning("Please enter the opponent name.")
            return

        if len(players) < 8:
            st.warning("Please enter at least 8 players.")
            return

        valid, message = validate_lineup(
            attack_players,
            defense_players,
            player_genders,
            competition_type,
        )

        if not valid:
            st.warning(message)
            return

        st.session_state.team = team.strip()
        st.session_state.opponent = opponent.strip()
        st.session_state.match_date = match_date

        st.session_state.competition_type = competition_type
        st.session_state.players = players.copy()
        st.session_state.player_genders = player_genders.copy()

        st.session_state.starting_attack = attack_players.copy()
        st.session_state.starting_defense = defense_players.copy()
        st.session_state.starting_bench = bench_players.copy()

        st.session_state.match_events = []

        rebuild_match_state()

        st.session_state.shot_x = None
        st.session_state.shot_y = None
        st.session_state.field_key_number += 1

        st.session_state.match_started = True

        st.rerun()


# ==================================================
# ACTIVE MATCH - SCORE AND LINEUP
# ==================================================

def show_score_and_lineup():

    st.success(
        f"{st.session_state.team} vs "
        f"{st.session_state.opponent}"
    )

    st.write(f"Date: {st.session_state.match_date}")

    col1, col2, col3 = st.columns(3)

    col1.metric(
        st.session_state.team,
        st.session_state.our_score,
    )

    col2.metric(
        st.session_state.opponent,
        st.session_state.opponent_score,
    )

    col3.metric(
        "Goals until zone switch",
        2 - st.session_state.goals_since_switch,
    )

    st.caption(
        f"Automatic zone switches: "
        f"{st.session_state.zone_switches}"
    )

    with st.expander("Current lineup", expanded=True):

        col1, col2, col3 = st.columns(3)

        with col1:

            st.write("**Attack**")

            for player in st.session_state.attack_players:
                st.write(f"- {player}")

        with col2:

            st.write("**Defense**")

            for player in st.session_state.defense_players:
                st.write(f"- {player}")

        with col3:

            st.write("**Bench**")

            if st.session_state.bench_players:

                for player in st.session_state.bench_players:
                    st.write(f"- {player}")

            else:
                st.write("No substitutes")


# ==================================================
# ACTIVE MATCH - SHOT LOCATION
# ==================================================

def show_shot_registration():

    st.subheader("Register action")

    st.write("Shot location")

    st.caption(
        "Click inside the field to select a shot location. "
        "The location can belong to our team or the opponent."
    )

    field_image = create_korfball_field()

    coordinates = streamlit_image_coordinates(
        field_image,
        width=FIELD_IMAGE_SIZE,
        key=(
            f"korfball_field_"
            f"{st.session_state.field_key_number}"
        ),
    )

    if coordinates is not None:

        click_x = coordinates["x"]
        click_y = coordinates["y"]

        margin = FIELD_MARGIN
        max_pixel = FIELD_IMAGE_SIZE - margin
        field_size = FIELD_IMAGE_SIZE - 2 * margin

        if (
            margin <= click_x <= max_pixel
            and margin <= click_y <= max_pixel
        ):

            new_x = (
                (click_x - margin)
                / field_size
                * FIELD_METRES
            )

            new_y = (
                (click_y - margin)
                / field_size
                * FIELD_METRES
            )

            if (
                new_x != st.session_state.shot_x
                or new_y != st.session_state.shot_y
            ):

                st.session_state.shot_x = new_x
                st.session_state.shot_y = new_y

                st.rerun()

    if (
        st.session_state.shot_x is not None
        and st.session_state.shot_y is not None
    ):

        register_shot_dialog()

    else:

        st.info(
            "Click on the field to select the shot location."
        )


# ==================================================
# ACTIVE MATCH - SUBSTITUTIONS
# ==================================================

def show_substitutions():

    st.subheader("Substitutions")

    if not st.session_state.bench_players:
        st.info("No players available on the bench.")
        return

    field_players = (
        st.session_state.attack_players
        + st.session_state.defense_players
    )

    # Select the outgoing player
    player_out = st.selectbox(
        "Player out",
        field_players,
        key="sub_player_out",
    )

    # Determine gender
    player_gender = st.session_state.player_genders[player_out]

    # Filter bench players by gender
    available_substitutes = [
        player
        for player in st.session_state.bench_players
        if st.session_state.player_genders[player] == player_gender
    ]

    # Use a different widget key for each outgoing player
    # so Streamlit updates the available options
    player_in = st.selectbox(
        "Player in",
        available_substitutes,
        index=0 if available_substitutes else None,
        placeholder="No eligible substitutes",
        key=f"sub_player_in_{player_out}",
    )

    if st.button(
        "Confirm substitution",
        disabled=player_in is None,
    ):

        substitute_player(
            player_out,
            player_in,
        )

# ==================================================
# ACTIVE MATCH - EVENT HISTORY
# ==================================================

def describe_event(event):

    if event["type"] == "substitution":

        return (
            f"Substitution: {event['player_out']} out, "
            f"{event['player_in']} in"
        )

    if event["team"] == "Our team":

        return (
            f"Our team: {event['player']} - "
            f"{event['shot_type']} - {event['result']}"
        )

    return (
        f"Opponent: {event['shot_type']} - "
        f"{event['result']} - "
        f"Defender: {event['player']}"
    )


def show_event_history():

    st.subheader("Match events")

    if not st.session_state.match_events:

        st.info("No match events registered yet.")
        return

    for index, event in enumerate(
        reversed(st.session_state.match_events),
        start=1,
    ):

        st.write(
            f"{index}. {describe_event(event)}"
        )

    if st.button("↩️ Undo last event"):

        undo_last_event()


# ==================================================
# ACTIVE MATCH - STATISTICS
# ==================================================

def show_statistics():

    st.subheader("Match statistics")

    own_shots = [
        action
        for action in st.session_state.actions
        if action["team"] == "Our team"
    ]

    opponent_shots = [
        action
        for action in st.session_state.actions
        if action["team"] == "Opponent"
    ]

    total_shots = len(own_shots)

    total_goals = sum(
        action["result"] == "Goal"
        for action in own_shots
    )

    goal_percentage = (
        total_goals / total_shots * 100
        if total_shots
        else 0
    )

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Our shots", total_shots)
    col2.metric("Our goals", total_goals)
    col3.metric("Shot %", f"{goal_percentage:.1f}%")
    col4.metric("Opponent shots", len(opponent_shots))

    # Player statistics
    st.subheader("Player statistics")

    player_stats = []

    for player in st.session_state.players:

        player_actions = [
            action
            for action in own_shots
            if action["player"] == player
        ]

        shots = len(player_actions)

        goals = sum(
            action["result"] == "Goal"
            for action in player_actions
        )

        player_stats.append({
            "Player": player,
            "Shots": shots,
            "Goals": goals,
            "Shot %": (
                round(goals / shots * 100, 1)
                if shots
                else 0
            ),
        })

    st.dataframe(
        pd.DataFrame(player_stats),
        hide_index=True,
        width="stretch",
    )

    # Defensive statistics
    st.subheader("Defensive statistics")

    defense_stats = []

    for player in st.session_state.players:

        defended_shots = [
            action
            for action in opponent_shots
            if action["player"] == player
        ]

        conceded = sum(
            action["result"] == "Goal"
            for action in defended_shots
        )

        defense_stats.append({
            "Player": player,
            "Opponent shots": len(defended_shots),
            "Goals conceded while involved": conceded,
        })

    st.dataframe(
        pd.DataFrame(defense_stats),
        hide_index=True,
        width="stretch",
    )

    st.caption(
        "A goal conceded while a defender is involved "
        "does not necessarily mean that defender was at fault."
    )

    # Shot type statistics
    st.subheader("Shot type statistics per player")

    analysis_player = st.selectbox(
        "Select player for analysis",
        st.session_state.players,
        key="analysis_player",
    )

    player_actions = [
        action
        for action in own_shots
        if action["player"] == analysis_player
    ]

    shot_type_stats = []

    for shot_type in SHOT_TYPES:

        actions = [
            action
            for action in player_actions
            if action["shot_type"] == shot_type
        ]

        shots = len(actions)

        if shots:

            goals = sum(
                action["result"] == "Goal"
                for action in actions
            )

            shot_type_stats.append({
                "Shot type": shot_type,
                "Shots": shots,
                "Goals": goals,
                "Shot %": round(
                    goals / shots * 100,
                    1,
                ),
            })

    if shot_type_stats:

        st.dataframe(
            pd.DataFrame(shot_type_stats),
            hide_index=True,
            width="stretch",
        )

    else:

        st.info(
            "No shots registered for this player yet."
        )


# ==================================================
# ACTIVE MATCH
# ==================================================

def show_active_match():

    show_score_and_lineup()

    st.divider()

    show_shot_registration()

    st.divider()

    show_substitutions()

    st.divider()

    show_event_history()

    st.divider()

    show_statistics()

    st.divider()

    if st.button("End match"):

        st.session_state.match_started = False
        st.session_state.shot_x = None
        st.session_state.shot_y = None
        st.session_state.field_key_number += 1

        st.rerun()


# ==================================================
# MAIN KORFBALL PAGE
# ==================================================

def show():

    initialize_state()

    st.header("Korfball")

    mode = st.radio(
        "Select mode:",
        ["Register match", "View analysis"],
        horizontal=True,
    )

    st.divider()

    if mode == "Register match":

        if not st.session_state.match_started:

            show_new_match()

        else:

            show_active_match()

    elif mode == "View analysis":

        st.subheader("Korfball analysis")

        st.info(
            "Saved match analysis will be available "
            "here in a later version."
        )

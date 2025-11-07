# -*- coding: utf-8 -*-

import os
import argparse
import json
import logging
import math
import chess
import chess.pgn
import chess.engine
import chess.variant
import concurrent.futures
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue


# Assign values to variables
ERROR_THRESHOLD = {
    "BLUNDER": -200,
    "MISTAKE": -100,
    "DUBIOUS": -50
}
NEEDS_ANNOTATION_THRESHOLD = 7.5
MAX_SCORE = 10000
MAX_CPL = 2000
SHORT_PV_LEN = 16

# Initialize Logging Module
logger = logging.getLogger(__name__)

if not logger.handlers:
    ch = logging.StreamHandler()
    logger.addHandler(ch)

logger.setLevel(logging.DEBUG)
# hldr = logging.FileHandler('annotator.log')
# formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# hldr.setFormatter(formatter)
# logger.addHandler(hldr)

# Uncomment this line to get EXTREMELY verbose UCI communication logging:
# logging.basicConfig(level=logging.DEBUG)
    

def sanitize_filename(filename):
    # Replace invalid characters with underscore
    invalid = '<>:"/\\|?*'
    for char in invalid:
        filename = filename.replace(char, '_')
    
    # Avoid reserved names
    reserved = ['CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 
                'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 'LPT3',
                'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9']
    # to match reserved names case insensitively
    if filename.upper() in reserved:
        filename = '_' + filename
        
    # Remove trailing periods or spaces
    filename = filename.rstrip('. ')
    
    # Limit length to 64 characters
    filename = filename[:64]
    
    return filename


def save_engine_config(engine, file_path):
    """Saves the current configuration of the engine to a JSON file."""
    config = {name: option.default for name, option in engine.options.items() if not option.is_managed()}
    with open(file_path, 'w') as f:
        json.dump(config, f)


def load_engine_config(engine, file_path):
    """Loads an engine configuration from a JSON file."""
    with open(file_path, 'r') as f:
        config = json.load(f)
    return {key: value for key, value in config.items() if key in engine.options and not engine.options[key].is_managed()}


def setup_engine(engine):
    """Sets up the engine with a given configuration."""
    # engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    config_path = f'{sanitize_filename(engine.id["name"])}.json'
    if os.path.exists(config_path):
        # If a config file exists, load it and apply to the engine
        config = load_engine_config(engine, config_path)
        engine.configure(config)
    else:
        # Otherwise, save the engine's default configuration
        save_engine_config(engine, config_path)
    return engine

# # Use it like this:
# engine = setup_engine('/path/to/stockfish')


def parse_args():
    """
    Define an argument parser and return the parsed arguments
    """
    parser = argparse.ArgumentParser(description='takes chess games in a PGN file and prints annotations to standard output')
    
    parser.add_argument("--file", "-f", help="input PGN file", required=True, metavar="FILE.pgn")
    parser.add_argument("--engine", "-e", help="analysis engine (default: %(default)s)", default="stockfish.exe", metavar="ENGINE_PATH")
    parser.add_argument("--gametime1", "-g1", help="time per move for stage 1 (default: %(default)s)", default=0.2, type=float, metavar="SECONDS")
    parser.add_argument("--gametime2", "-g2", help="time per move for stage 2 (default: %(default)s)", default=2.0, type=float, metavar="SECONDS")
    parser.add_argument("--threads", "-t", help="number of threads (default: %(default)s)", default=1, type=int, metavar="THREADS")
    parser.add_argument("--hash-size", "-hs", help="hash size in MB (default: %(default)s)", default=128, type=int, metavar="HASH_SIZE")
    parser.add_argument("--num-workers", "-nw", help="number of worker processes (default: %(default)s)", default=1, type=int, metavar="WORKERS")
    parser.add_argument("--output-file", "-o", help="output PGN file (default: %(default)s)", default="analyzed_games.pgn", metavar="OUTPUT_FILE.pgn")
    parser.add_argument("--verbose", "-v", help="increase verbosity", action="count", default=0)
    
    return parser.parse_args()


def setup_logging(args):
    """
    Sets logging module verbosity according to runtime arguments
    """
    
    if args.verbose >= 3:
        logger.setLevel(logging.DEBUG)
        hldr = logging.FileHandler('annotator.log')
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        hldr.setFormatter(formatter)
        logger.addHandler(hldr)
    elif args.verbose == 2:
        logger.setLevel(logging.DEBUG)
    elif args.verbose == 1:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)


# def setup_logging():
#     logger.setLevel(logging.DEBUG)
    

def eval_numeric(info_handler):
    """
    Returns a numeric evaluation of the position, even if depth-to-mate was
    found. This facilitates comparing numerical evaluations with depth-to-mate
    evaluations
    """
    dtm = info_handler["score"].relative.mate()
    cp = info_handler["score"].relative.score()

    if dtm is not None:
        # We have depth-to-mate (dtm), so translate it into a numerical
        # evaluation. This number needs to be just big enough to guarantee that
        # it is always greater than a non-dtm evaluation.

        if dtm >= 1:
            return MAX_SCORE - dtm
        else:
            return -(MAX_SCORE + dtm)

    elif cp is not None:
        # We don't have depth-to-mate, so return the numerical evaluation (in centipawns)
        return cp

    # If we haven't returned yet, then the info_handler had garbage in it
    raise RuntimeError("Evaluation found in the info_handler was unintelligible")


def eval_human(white_to_move, info_handler):
    """
    Returns a human-readable evaluation of the position:
        If depth-to-mate was found, return plain-text mate announcement
        (e.g. "Mate in 4")
        If depth-to-mate was not found, return an absolute numeric evaluation
    """
    dtm = info_handler["score"].relative.mate()
    cp = info_handler["score"].relative.score()

    if dtm is not None:
        return "Mate in {}".format(abs(dtm))
    elif cp is not None:
        # We don't have depth-to-mate, so return the numerical evaluation (in
        # pawns)
        return '{:.2f}'.format(eval_absolute(cp / 100, white_to_move))

    # If we haven't returned yet, then the info_handler had garbage in it
    raise RuntimeError("Evaluation found in the info_handler was unintelligible")


def eval_absolute(number, white_to_move):
    """
    Accepts a relative evaluation (from the point of view of the player to
    move) and returns an absolute evaluation (from the point of view of white)
    """

    return number if white_to_move else -number


def winning_chances(centipawns):
    """
    Takes an evaluation in centipawns and returns an integer value estimating
    the chance the player to move will win the game

    winning chances = 50 + 50 * (2 / (1 + e^(-0.004 * centipawns)) - 1)
    """
    return 50 + 50 * (2 / (1 + math.exp(-0.004 * centipawns)) - 1)


def needs_annotation(judgment):
    """
    Returns a boolean indicating whether a node with the given evaluations
    should have an annotation added
    """
    best = winning_chances(int(judgment["besteval"]))
    played = winning_chances(int(judgment["playedeval"]))
    delta = best - played

    return delta > NEEDS_ANNOTATION_THRESHOLD


def judge_move(board, played_move, engine, searchtime):
    """
    Evaluate the strength of a given move by comparing it to engine's best
    move and evaluation at a given depth, in a given board context

    Returns a judgment

    A judgment is a dictionary containing the following elements:
          "bestmove":      The best move in the position, according to the engine
          "besteval":      A numeric evaluation of the position after the best move is played
          "bestcomment":   A plain-text comment appropriate for annotating the best move
          "pv":            The engine's primary variation including the best move
          "playedeval":    A numeric evaluation of the played move
          "playedcomment": A plain-text comment appropriate for annotating the played move
          "depth":         Search depth in plies
          "nodes":         Number nodes searched
    """

    judgment = {}

    # First, get the engine bestmove and evaluation
    info_handler = engine.analyse(board, chess.engine.Limit(time=searchtime))

    judgment["bestmove"] = info_handler["pv"][0]
    judgment["besteval"] = eval_numeric(info_handler)
    judgment["pv"] = info_handler["pv"]
    judgment["depth"] = info_handler["depth"]
    judgment["nodes"] = info_handler["nodes"]
    judgment["time"] = info_handler["time"]

    # Annotate the best move
    judgment["bestcomment"] = eval_human(board.turn, info_handler)

    # If the played move matches the engine bestmove, we're done
    if played_move == judgment["bestmove"]:
        judgment["playedeval"] = judgment["besteval"]
    else:
        # get the engine evaluation of the played move
        board.push(played_move)
        info_handler = engine.analyse(board, chess.engine.Limit(time=searchtime))

        # Store the numeric evaluation.
        # We invert the sign since we're now evaluating from the opponent's perspective
        judgment["playedeval"] = -eval_numeric(info_handler)
            
        # Take the played move off the stack (reset the board)
        board.pop()

    # Annotate the played move
    judgment["playedcomment"] = eval_human(not board.turn, info_handler)

    return judgment


def get_nags(judgment):
    """
    Returns a Numeric Annotation Glyph (NAG) according to how much worse the
    played move was vs the best move
    """

    delta = judgment["playedeval"] - judgment["besteval"]

    if delta < ERROR_THRESHOLD["BLUNDER"]:
        return [chess.pgn.NAG_BLUNDER]
    elif delta < ERROR_THRESHOLD["MISTAKE"]:
        return [chess.pgn.NAG_MISTAKE]
    elif delta < ERROR_THRESHOLD["DUBIOUS"]:
        return [chess.pgn.NAG_DUBIOUS_MOVE]
    else:
        return []


def var_end_comment(board, judgment):
    """
    Return a human-readable annotation explaining the board state (if the game
    is over) or a numerical evaluation (if it is not)
    """
    score = judgment["bestcomment"]
    depth = judgment["depth"]

    if board.is_stalemate():
        return "Stalemate"
    elif board.is_insufficient_material():
        return "Insufficient material to mate"
    elif board.can_claim_fifty_moves():
        return "Fifty move rule"
    elif board.can_claim_threefold_repetition():
        return "Three-fold repetition"
    elif board.is_checkmate():
        # checkmate speaks for itself
        return ""
    return "{}/{}".format(str(score), str(depth))


def truncate_pv(board, pv):
    """
    If the pv ends the game, return the full pv. 
    Otherwise, return the pv truncated to 10 half-moves
    """

    for move in pv:
        if not board.is_legal(move):
            raise AssertionError
        board.push(move)

    if board.is_game_over(claim_draw=True):
        return pv
    else:
        return pv[:SHORT_PV_LEN]


def add_annotation(node, judgment):
    """
    Add evaluations and the engine's primary variation as annotations to a node
    """
    prev_node = node.parent

    # Add the engine evaluation
    if judgment["bestmove"] != node.move:
        node.comment = judgment["playedcomment"]

    # Get the engine primary variation
    variation = truncate_pv(prev_node.board(), judgment["pv"])

    # Add the engine's primary variation as an annotation
    prev_node.add_line(moves=variation)

    # Add a comment to the end of the variation explaining the game state
    var_end_node = prev_node.variation(judgment["pv"][0]).end()
    var_end_node.comment = var_end_comment(var_end_node.board(), judgment)

    # Add a Numeric Annotation Glyph (NAG) according to how weak the played
    # move was
    node.nags = get_nags(judgment)


def classify_fen(fen, ecodb):
    """
    Searches a JSON file with Encyclopedia of Chess Openings (ECO) data to
    check if the given FEN matches an existing opening record

    Returns a classification

    A classfication is a dictionary containing the following elements:
        "code":         The ECO code of the matched opening
        "desc":         The long description of the matched opening
        "path":         The main variation of the opening
    """
    classification = {}
    classification["code"] = ""
    classification["desc"] = ""
    classification["path"] = ""

    for opening in ecodb:
        if opening['f'] == fen:
            classification["code"] = opening['c']
            classification["desc"] = opening['n']
            classification["path"] = opening['m']

    return classification


def eco_fen(board):
    """
    Takes a board position and returns a FEN string formatted for matching with
    eco.json
    """
    return board.epd()


def debug_print(node, judgment):
    """
    Prints some debugging info about a position that was just analyzed
    """
    
    logger.debug(f"\n{node.parent.board()}")
    logger.debug(node.parent.board().fen())
    logger.debug("Best move: %s", format(node.parent.board().san(judgment["bestmove"])))
    logger.debug("Best eval: %s", format(judgment["besteval"]))
    logger.debug("Best comment: %s", format(judgment["bestcomment"]))
    logger.debug("PV: %s", format(node.parent.board().variation_san(judgment["pv"])))
    logger.debug("Played move: %s", format(node.parent.board().san(node.move)))
    logger.debug("Played eval: %s", format(judgment["playedeval"]))
    logger.debug("Played comment: %s", format(judgment["playedcomment"]))
    logger.debug("Delta: %s", format(judgment["besteval"] - judgment["playedeval"]))
    logger.debug("Depth: %s", format(judgment["depth"]))
    logger.debug("Nodes: %s", format(judgment["nodes"]))
    logger.debug("Time: %s", format(judgment["time"]))
    logger.debug("Needs annotation: %s", format(needs_annotation(judgment)))
    logger.debug("")


def cpl(string):
    """
    Centipawn Loss
    Takes a string and returns an integer representing centipawn loss of the
    move We put a ceiling on this value so that big blunders don't skew the
    acpl too much
    """

    cpl = int(string)

    return min(cpl, MAX_CPL)


def acpl(cpl_list):
    """
    Average Centipawn Loss
    Takes a list of integers and returns an average of the list contents
    """
    try:
        return sum(cpl_list) / len(cpl_list)
    except ZeroDivisionError:
        return 0


def clean_game(game):
    """
    Takes a game and strips all comments and variations, returning the
    "cleaned" game
    """
    node = game.end()

    while True:
        prev_node = node.parent

        node.comment = None
        node.nags = []
        for variation in reversed(node.variations):
            if not variation.is_main_variation():
                node.remove_variation(variation)

        if node == game.root():
            break

        node = prev_node

    return node.root()


def game_length(game):
    """
    Takes a game and returns an integer corresponding to the number of
    half-moves in the game
    """
    ply_count = 0
    node = game.end()

    while not node == game.root():
        node = node.parent
        ply_count += 1

    return ply_count


def classify_opening(game):
    """
    Takes a game and adds an ECO code classification for the opening
    Returns the classified game and root_node, which is the node where the
    classification was made
    """
    ecopath = os.path.join(os.path.dirname(__file__), 'eco/eco.json')
    with open(ecopath, 'r') as ecofile:
        ecodata = json.load(ecofile)

        ply_count = 0

        root_node = game.root()
        node = game.end()

        # Opening classification for variant games is not implemented (yet?)
        is_960 = root_node.board().chess960
        if is_960:
            variant = "chess960"
        else:
            variant = type(node.board()).uci_variant

        if variant != "chess":
            logger.info("Skipping opening classification in variant game: {}".format(variant))
            return node.root(), root_node, game_length(game)

        logger.info("Classifying the opening for non-variant {} game...".format(variant))

        while not node == game.root():
            prev_node = node.parent

            fen = eco_fen(node.board())
            classification = classify_fen(fen, ecodata)

            if classification["code"] != "":
                # Add some comments classifying the opening
                node.root().headers["ECO"] = classification["code"]
                node.root().headers["Opening"] = classification["desc"]
                node.comment = "{} {}".format(classification["code"], classification["desc"])
                # Remember this position so we don't analyze the moves
                # preceding it later
                root_node = node
                # Break (don't classify previous positions)
                break

            ply_count += 1
            node = prev_node

        return node.root(), root_node, ply_count


def add_acpl(game, root_node):
    """
    Takes a game and a root node, and adds PGN headers with the computed ACPL
    (average centipawn loss) for each player. Returns a game with the added
    headers.
    """
    white_cpl = []
    black_cpl = []

    node = game.end()
    while not node == root_node:
        prev_node = node.parent

        judgment = node.comment
        delta = judgment["besteval"] - judgment["playedeval"]

        if node.board().turn:
            black_cpl.append(cpl(delta))
        else:
            white_cpl.append(cpl(delta))

        node = prev_node

    node.root().headers["WhiteACPL"] = str(round(acpl(white_cpl)))
    node.root().headers["BlackACPL"] = str(round(acpl(black_cpl)))

    return node.root()


def analyze_game(game, arg_gametime, enginepath, threads, hash_size):
    """
    Take a PGN game and return a GameNode with engine analysis added
    - Attempt to classify the opening with ECO and identify the root node
        * The root node is the position immediately after the ECO
        classification
        * This allows us to skip analysis of moves that have an ECO
        classification
    - Analyze the game, adding annotations where appropriate
    - Return the root node with annotations
    """
    
    # First, check the game for PGN parsing errors
    # This is done so that we don't waste CPU time on nonsense games
    checkgame(game)

    # Initialize the engine
    try:
        engine = chess.engine.SimpleEngine.popen_uci(enginepath)
    except FileNotFoundError:
        errormsg = "Engine '{}' was not found. Aborting...".format(enginepath)
        logger.critical(errormsg)
        raise
    except PermissionError:
        errormsg = "Engine '{}' could not be executed. Aborting...".format(enginepath)
        logger.critical(errormsg)
        raise

    if game.board().uci_variant != "chess" or game.root().board().chess960:
        # This is a variant game, so confirm that the engine we're using supports the variant.
        if game.root().board().chess960:
            try:
                engine.options["UCI_Chess960"]
            except KeyError:
                message = "UCI_Chess960 is not supported by the engine and this is a chess960 game."
                logger.critical(message)
                raise RuntimeError(message)

        if game.board().uci_variant != "chess":
            try:
                engine_variants = engine.options["UCI_Variant"].var
                if not game.board().uci_variant in engine_variants:
                    raise AssertionError
            except KeyError:
                message = "UCI_Variant option is not supported by the engine and this is a variant game."
                logger.critical(message)
                raise RuntimeError(message)
            except AssertionError:
                message = "Variant {} is not supported by the engine.".format(game.board().uci_variant)
                logger.critical(message)
                raise RuntimeError(message)

        # Now that engine support for the variant is confirmed, set engine UCI
        # options as appropriate for the variant
        engine.configure({
            "UCI_Variant": game.board().uci_variant,
            "UCI_Chess960": game.board().chess960,
            "Threads": threads,
            "Hash": hash_size
        })
    else:
        engine.configure({
            "Threads": threads,
            "Hash": hash_size
            # "UCI_ShowWDL": True
        })

    # Start keeping track of the root node. This will change if we successfully classify the opening
    root_node = game.end()
    node = root_node

    # Clear existing comments and variations
    game = clean_game(game)

    # Attempt to classify the opening and calculate the game length
    game, root_node, ply_count = classify_opening(game)

    # Perform game analysis
    
    # First pass:
    #
    #   - Performs a shallow-depth search to the root node
    #   - Leaves annotations showing the centipawn loss of each move
    #
    # These annotations form the basis of the second pass, which will analyze
    # those moves that had a high centipawn loss (mistakes)

    # We have a fraction of the total budget to finish the first pass
    # pass1_budget = get_pass1_budget(budget)
    # time_per_move = get_time_per_move(pass1_budget, ply_count)
    
    time_per_move = arg_gametime[0]
    pass1_budget = time_per_move * ply_count
    logger.debug("Pass 1 budget is %i seconds, with %f seconds per move", pass1_budget, time_per_move)

    # Loop through the game doing shallow analysis
    logger.info("Performing first pass...")

    # Count the number of mistakes that will have to be annotated later
    error_count = 0

    node = game.end()
    while not node == root_node:
        prev_node = node.parent

        # Get the engine judgment of the played move in this position
        judgment = judge_move(prev_node.board(), node.move, engine, time_per_move)

        # Record the delta, to be referenced in the second pass
        node.comment = judgment

        # Count the number of mistakes that will have to be annotated later
        if needs_annotation(judgment):
            error_count += 1

        # Print some debugging info
        debug_print(node, judgment)

        node = prev_node

    # Calculate the average centipawn loss (ACPL) for each player
    game = add_acpl(game, root_node)

    # Second pass:
    #
    #   - Iterate through the comments looking for moves with high centipawn
    #   loss
    #   - Leaves annotations on those moves showing what the player could have
    #   done instead
    #

    time_per_move = arg_gametime[1]
    pass2_budget = time_per_move * error_count

    if error_count == 0:
        logger.debug("No errors found on first pass!")
        # There were no mistakes in the game, so deeply analyze all the moves
        pass2_budget = time_per_move * ply_count
        node = game.end()
        while not node == root_node:
            prev_node = node.parent
            # Reset the comments to a value high enough to ensure that they all get analyzed
            comment = {}
            comment["besteval"] = MAX_SCORE - 1
            comment["playedeval"] = -(MAX_SCORE - 1)
            
            node.comment = comment
            node = prev_node
    
    logger.debug("Pass 2 budget is %i seconds, with %f seconds per move", pass2_budget, time_per_move)
    
    # Loop through the game doing deep analysis on the flagged moves
    logger.info("Performing second pass...")

    node = game.end()
    while not node == root_node:
        prev_node = node.parent

        judgment = node.comment

        if needs_annotation(judgment):
            # Get the engine judgment of the played move in this position
            judgment = judge_move(prev_node.board(), node.move, engine, time_per_move)

            # Verify that the engine still dislikes the played move
            if needs_annotation(judgment):
                add_annotation(node, judgment)
            else:
                node.comment = None

            # Print some debugging info
            debug_print(node, judgment)
        else:
            node.comment = None

        node = prev_node

    annotator = engine.id["name"] if engine.id["name"] else ""
    node.root().comment = annotator
    node.root().headers["Annotator"] = annotator
    engine.quit()
    
    return node.root()


def checkgame(game):
    """
    Check for PGN parsing errors and abort if any were found
    This prevents us from burning up CPU time on nonsense positions
    """
    if game.errors:
        errormsg = "There were errors parsing the PGN game:"
        logger.critical(errormsg)
        for error in game.errors:
            logger.critical(error)
        logger.critical("Aborting...")
        raise RuntimeError(errormsg)

    # Try to verify that the PGN file was readable
    if game.end().parent is None:
        errormsg = "Could not render the board. Is the file legal PGN? Aborting..."
        logger.critical(errormsg)
        raise RuntimeError(errormsg)


def worker(game, gametime, engine, threads, hash_size):
    """
    Worker function to analyze a single game.
    """

    try:
        logger.info('Worker started with game')
        logger.info('Worker parameters: gametime=%s, engine=%s, threads=%s, hash_size=%s', gametime, engine, threads, hash_size)
        
        # Simulate some work
        analyzed_game = analyze_game(game, gametime, engine, threads, hash_size)
        
        logger.info('Worker finished analysis')
        return analyzed_game
    except KeyboardInterrupt:
        logger.critical("Received KeyboardInterrupt.")
        raise
    except Exception as e:
        logger.critical("An unhandled exception occurred: {}".format(type(e)))
        raise e


def main_precedure(args, progress_queue):
    """
    Analyzes chess games from a PGN file and updates progress via a queue.
    """
    pgnfile = args.file
    engine_path = args.engine
    gametime = (args.gametime1, args.gametime2)
    threads = args.threads
    hash_size = args.hash_size
    num_workers = args.num_workers
    output_pgnfile = args.output_file

    logger.info("Loading games from PGN file...")
    try:
        with open(pgnfile, encoding='utf-8') as pgn:
            games = list(iter(lambda: chess.pgn.read_game(pgn), None))
    except Exception as e:
        progress_queue.put(('error', f"Failed to read PGN file: {e}"))
        return

    total_games = len(games)
    logger.info(f"Total games loaded: {total_games}")
    progress_queue.put(('total', total_games))

    try:
        # with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
        #     submitted_futures = [
        #         executor.submit(worker, game, gametime, engine_path, threads, hash_size) 
        #         for game in games
        #     ]
                      
        #     with open(output_pgnfile, 'w', encoding='utf-8') as out_pgn:
        #         for i, future in enumerate(submitted_futures, 1):
        #             try:
        #                 analyzed_game = future.result()
        #                 exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
        #                 analyzed_game.accept(exporter)
        #                 out_pgn.write(str(exporter) + '\n\n')
        #                 progress_queue.put(('progress', i))
        #             except Exception as e:
        #                 logger.error(f"Exception occurred while analyzing game {i}: {e}")
        #                 progress_queue.put(('error', f"Exception in game {i}: {e}"))
        #                 return
        
        # Use ProcessPoolExecutor to manage worker processes
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            # Map each game to its corresponding future
            future_to_game = {executor.submit(worker, game, gametime, engine_path, threads, hash_size): game for game in games}
            
            # Open the output PGN file for writing
            with open(output_pgnfile, 'w', encoding='utf-8') as out_pgn:
                # total_games = len(future_to_game)  # Get the total number of games
                for i, future in enumerate(concurrent.futures.as_completed(future_to_game), start=1):
                    try:
                        analyzed_game = future.result()  # Get the result of the analysis
                        exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
                        analyzed_game.accept(exporter)  # Export the analyzed game
                        out_pgn.write(str(exporter) + '\n\n')  # Write to the output file
                        
                        # Update progress
                        progress_queue.put(('progress', i))  # Send progress update to the queue
                    except Exception as e:
                        logger.error(f"Exception occurred while analyzing game {i}: {e}")
                        progress_queue.put(('error', f"Exception in game {i}: {e}"))  # Send error message to the queue
                        return  # Exit if an error occurs

    except PermissionError:
        errormsg = "Input file not readable. Aborting..."
        logger.critical(errormsg)
        progress_queue.put(('error', errormsg))
    except KeyboardInterrupt:
        logger.critical("Received KeyboardInterrupt.")
        progress_queue.put(('error', "Analysis interrupted by user."))
    except Exception as e:
        logger.critical(f"An unhandled exception occurred: {e}")
        progress_queue.put(('error', f"Unhandled exception: {e}"))
        

class ChessAnalysisGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Chess Analysis Launcher")
        self.root.geometry("700x500")
        
        # Initialize queue for progress updates
        self.progress_queue = queue.Queue()

        # Create UI components
        self.create_widgets()

        # Set up periodic call to check queue
        self.root.after(100, self.process_queue)

    def create_widgets(self):
        padding_x = 10
        padding_y = 5

        # PGN File
        tk.Label(self.root, text="PGN File:").grid(row=0, column=0, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.pgn_entry = tk.Entry(self.root, width=50)
        self.pgn_entry.grid(row=0, column=1, padx=padding_x, pady=padding_y)
        tk.Button(self.root, text="Browse", command=self.browse_pgn).grid(row=0, column=2, padx=padding_x, pady=padding_y)

        # Engine Path
        tk.Label(self.root, text="Engine Executable:").grid(row=1, column=0, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.engine_entry = tk.Entry(self.root, width=50)
        self.engine_entry.grid(row=1, column=1, padx=padding_x, pady=padding_y)
        tk.Button(self.root, text="Browse", command=self.browse_engine).grid(row=1, column=2, padx=padding_x, pady=padding_y)

        # Game Time 1
        tk.Label(self.root, text="Game Time 1 (s):").grid(row=2, column=0, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.gametime1_entry = tk.Entry(self.root, width=20)
        self.gametime1_entry.grid(row=2, column=1, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.gametime1_entry.insert(0, "1.0")

        # Game Time 2
        tk.Label(self.root, text="Game Time 2 (s):").grid(row=3, column=0, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.gametime2_entry = tk.Entry(self.root, width=20)
        self.gametime2_entry.grid(row=3, column=1, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.gametime2_entry.insert(0, "10.0")

        # Threads
        tk.Label(self.root, text="Threads:").grid(row=4, column=0, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.threads_entry = tk.Entry(self.root, width=20)
        self.threads_entry.grid(row=4, column=1, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.threads_entry.insert(0, "1")

        # Hash Size
        tk.Label(self.root, text="Hash Size (MB):").grid(row=5, column=0, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.hash_size_entry = tk.Entry(self.root, width=20)
        self.hash_size_entry.grid(row=5, column=1, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.hash_size_entry.insert(0, "128")

        # Number of Workers
        tk.Label(self.root, text="Number of Workers:").grid(row=6, column=0, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.num_workers_entry = tk.Entry(self.root, width=20)
        self.num_workers_entry.grid(row=6, column=1, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.num_workers_entry.insert(0, "4")

        # Output PGN File
        tk.Label(self.root, text="Output PGN File:").grid(row=7, column=0, sticky=tk.W, padx=padding_x, pady=padding_y)
        self.output_entry = tk.Entry(self.root, width=50)
        self.output_entry.grid(row=7, column=1, padx=padding_x, pady=padding_y)
        tk.Button(self.root, text="Browse", command=self.browse_output).grid(row=7, column=2, padx=padding_x, pady=padding_y)

        # Start Button
        self.start_button = tk.Button(self.root, text="Start Analysis", command=self.start_analysis, bg="green", fg="white", font=("Helvetica", 12, "bold"))
        self.start_button.grid(row=8, column=1, pady=20)

        # Progress Bar
        self.progress = ttk.Progressbar(self.root, orient='horizontal', mode='determinate', length=600)
        self.progress.grid(row=9, column=0, columnspan=3, padx=padding_x, pady=10)

        # Status Label
        self.status_label = tk.Label(self.root, text="Status: Idle", anchor="w")
        self.status_label.grid(row=10, column=0, columnspan=3, sticky="w", padx=padding_x, pady=5)

    def browse_pgn(self):
        filepath = filedialog.askopenfilename(filetypes=[("PGN files", "*.pgn"), ("All files", "*.*")])
        if filepath:
            self.pgn_entry.delete(0, tk.END)
            self.pgn_entry.insert(0, filepath)

    def browse_engine(self):
        filepath = filedialog.askopenfilename(filetypes=[("Executables", "*.exe" if os.name == 'nt' else ("All files", "*.*"))])
        if filepath:
            self.engine_entry.delete(0, tk.END)
            self.engine_entry.insert(0, filepath)

    def browse_output(self):
        filepath = filedialog.asksaveasfilename(defaultextension=".pgn", filetypes=[("PGN files", "*.pgn"), ("All files", "*.*")])
        if filepath:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, filepath)

    def start_analysis(self):
        # Gather input parameters
        pgn_file = self.pgn_entry.get()
        engine_path = self.engine_entry.get()
        gametime1 = self.gametime1_entry.get()
        gametime2 = self.gametime2_entry.get()
        threads = self.threads_entry.get()
        hash_size = self.hash_size_entry.get()
        num_workers = self.num_workers_entry.get()
        output_file = self.output_entry.get()

        # Validate inputs
        if not os.path.isfile(pgn_file):
            messagebox.showerror("Error", "Invalid PGN file path.")
            return
        if not os.path.isfile(engine_path):
            messagebox.showerror("Error", "Invalid engine executable path.")
            return
        if not output_file:
            messagebox.showerror("Error", "Output file not specified.")
            return

        try:
            gametime1 = float(gametime1)
            gametime2 = float(gametime2)
            threads = int(threads)
            hash_size = int(hash_size)
            num_workers = int(num_workers)
        except ValueError:
            messagebox.showerror("Error", "Game time must be a float, and threads, hash size, and number of workers must be integers.")
            return

        # Disable the start button to prevent multiple starts
        self.start_button.config(state=tk.DISABLED)
        self.status_label.config(text="Status: Running")

        # Start analysis in a separate thread
        args = argparse.Namespace(
            file=pgn_file,
            engine=engine_path,
            gametime1=gametime1,
            gametime2=gametime2,
            threads=threads,
            hash_size=hash_size,
            num_workers=num_workers,
            output_file=output_file
        )

        analysis_thread = threading.Thread(target=main_precedure, args=(args, self.progress_queue))
        analysis_thread.start()

    def process_queue(self):
        """
        Processes messages from the analysis thread and updates the GUI accordingly.
        """
        try:
            while True:
                msg_type, data = self.progress_queue.get_nowait()
                if msg_type == 'total':
                    total_games = data
                    self.progress['maximum'] = total_games
                elif msg_type == 'progress':
                    current = data
                    self.progress['value'] = current
                    self.status_label.config(text=f"Status: Processing {current} / {self.progress['maximum']}")
                elif msg_type == 'error':
                    messagebox.showerror("Error", data)
                    self.status_label.config(text="Status: Error")
                    self.start_button.config(state=tk.NORMAL)
                elif msg_type == 'complete':
                    self.progress['value'] = self.progress['maximum']
                    self.status_label.config(text="Status: Complete")
                    self.start_button.config(state=tk.NORMAL)
        except queue.Empty:
            pass
        # Schedule the next queue check
        self.root.after(100, self.process_queue)


def main():
    root = tk.Tk()
    app = ChessAnalysisGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
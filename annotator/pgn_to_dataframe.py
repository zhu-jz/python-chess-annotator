# -*- coding: utf-8 -*-
"""
Created on Tue Dec 12 16:22:26 2023

@author: ZHU
"""

import chess.pgn
import pandas as pd
import os
import io
import logging
from pathlib import Path
from tqdm import tqdm


# # Create a logger
# logger = logging.getLogger('myLogger')
# logger.setLevel(logging.INFO)

# # Create file handler which logs even debug messages
# fh = logging.FileHandler('encoding_errors.log')
# fh.setLevel(logging.INFO)

# # Create formatter and add it to the handlers
# formatter = logging.Formatter('%(asctime)s\t%(levelname)s\t%(message)s', datefmt='%Y-%m-%d %H:%M:%S')
# fh.setFormatter(formatter)

# # Add the handlers to the logger
# logger.addHandler(fh)

# Configure logging with a tabular format
logging.basicConfig(
    filename='encoding_errors.log',
    level=logging.INFO,
    format='%(asctime)s\t%(levelname)s\t%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

    
def list_files(directory, file_formats=['*.pgn']):
    path = Path(directory)
    files = []
    
    for file_format in file_formats:
        search_pattern = f'**/{file_format}'
        files.extend(path.glob(search_pattern))
        
    sorted_files = sorted(files, key=os.path.getmtime)
    return sorted_files


def write_error_game(error_file_path, game):
    with open(error_file_path, 'a') as error_file:
        exporter = chess.pgn.FileExporter(error_file)
        game.accept(exporter)


def my_open(file_path, encoding='utf-8'):    
    problematic_lines = []
    file_data = []
    
    try:
        with open(file_path, 'rb') as file:
            for line in file:
                try:
                    decoded_line = line.decode(encoding)
                    file_data.append(decoded_line)
                except UnicodeDecodeError as e:
                    position = file.tell()
                    problematic_lines.append((position, line, str(e)))
                    file_data.append(line.decode(encoding, 'replace'))
    
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return None
    
    if problematic_lines:
        logging.info(f"Read {file_path} with some characters replaced due to encoding errors.")
        logging.info(f"Found {len(problematic_lines)} line(s) with problematic encoding.")
        for line_info in problematic_lines:
            position, line, error = line_info    
            logging.error(f"Position {position}:{error}")
            logging.info(f"Raw line: {line}")

    # # Close the file handler
    # logger.removeHandler(fh)
    # fh.close()

    return ''.join(file_data)


def pgn_to_dataframe(file, progress_bar):
    file_content = my_open(file, encoding='ISO-8859-1')
    
    if not file_content:
        return None
    
    pgn = io.StringIO(file_content)
    games_data = []
    game_count = 0
    
    while True:
        game = chess.pgn.read_game(pgn)
        if game is None:
            break
            
        game_info = dict(game.headers)
        game_info["Moves"] = str(game.mainline())
        # game_info["obj"] = game
        
        if game.errors:
            game_info["Errors"] = '; '.join(str(e) for e in game.errors)
            
        games_data.append(game_info)
        game_count += 1
        progress_bar.set_postfix(file=os.path.basename(file), games_processed=game_count, refresh=True)
        
    df = pd.DataFrame(games_data)
    return df


# Use the function
directory = r'D:\Users\ZHU\Downloads\twic\update\pgn'

li = []
files = list_files(directory)
with tqdm(files, desc="Processing files", position=0) as pbar:
    for file in pbar:
        df = pgn_to_dataframe(file, progress_bar=pbar)
        li.append(df)

# file = r'D:/Users/ZHU/Downloads/twic/pgn/twic1247.pgn'
# files = [file]
# with tqdm(files, desc="Processing files", position=0) as pbar:
#     for file in pbar:
#         df = pgn_to_dataframe(file, progress_bar=pbar)

# # After all logging is done
# for handler in logger.handlers:
#     handler.flush()
#     handler.close()

#%%

# def my_open(file_path, encoding='utf-8'):
#     # Try to read the entire file
#     try:
#         with open(file_path, 'rb') as f:
#             file_data = f.read().decode(encoding, 'strict')
#         return file_data
#     except UnicodeDecodeError as e:
#         print(f"Error while decoding the entire file: {e}")

#         # Process the file line by line to find the problematic line
#         line_number = 1
#         try:
#             with open(file_path, 'rb') as file:
#                 for line in file:
#                     line.decode(encoding)
#                     line_number += 1
#         except UnicodeDecodeError as e:
#             print(f"Error on line {line_number}: {e}")
#             print(f"Raw line: {line}")
#             return None

#%%

# # Example usage
# file_path = r'D:\Users\ZHU\Downloads\twic\pgn\twic1033.pgn'
# file_content = my_open(file_path, encoding='gb18030')

# if file_content:
#     print("File processed successfully.")
# else:
#     print("Failed to read the file due to encoding errors. Check the log for details.")



#%%
import chardet

with open(r'D:\Users\ZHU\Downloads\twic\update\pgn\twic1528.pgn', 'rb') as file:
    raw_data = file.read()
    encoding = chardet.detect(raw_data)['encoding']
    print(f"Detected encoding: {encoding}")


#%%


# import chess.pgn
# import io

def dataframe_to_games(df):
    games = []
    for _, row in df.iterrows():
        game_info = "\n".join([f"[{k} \"{v}\"]" for k, v in row.items() if (k != 'Moves') and (not pd.isnull(v))])
        game_info += "\n\n" + row['Moves']
        pgn = io.StringIO(game_info)
        game = chess.pgn.read_game(pgn)
        games.append(game)
    return games

# Use the function
games = dataframe_to_games(df)
for game in games:
    print(game)

#%%

import re

# Define the path to your log file
log_file_path = r'D:\Users\ZHU\Downloads\twic\encoding_errors.log'

# Regular expression pattern to match lines and capture file paths
# This pattern specifically captures the file path ending in ".pgn"
pattern = re.compile(r'INFO\s+Read\s+(D:\\.*?\.pgn)')

# List to hold the file paths
file_paths = []

# Open and read the log file
with open(log_file_path, 'r', encoding='utf-8') as file:
    for line in file:
        # Search for the pattern in each line
        match = pattern.search(line)
        if match:
            # If a match is found, extract the file path (first capturing group)
            file_path = match.group(1)
            file_paths.append(file_path)


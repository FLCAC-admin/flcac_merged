import logging
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_dir

from ldpm import __path__ as root_path


# %% paths container
ROOT_PATH = Path(*root_path)

@dataclass
class Paths:
    root: Path = ROOT_PATH
    config: Path = ROOT_PATH / 'config'
    project: Path = ROOT_PATH.parent
    output: Path = ROOT_PATH.parent / 'output'
    cache: Path = field(init=False)
    
    def __post_init__(self) -> None:
        self.output.mkdir(parents=True, exist_ok=True)

        # Cache FLCAC data packages in ~/LDPM/FLCAC
            # on Windows, C:\Users\<user>\AppData\Local\LDPM\FLCAC
        self.cache = Path(user_data_dir(appauthor='LDPM', appname='FLCAC'))  
        self.cache.mkdir(parents=True, exist_ok=True)

PATHS = Paths()


# %% logging
LOGGING_CONFIG = {
    'version': 1,
    'loggers': {
        'uslci_plus': {  # root logger
            'handlers': ['console', 'file_info', 'file_debug'],
            # 'level': 'INFO',
        },
        'utils': {
            'handlers': ['console', 'file_info', 'file_debug'],
            # 'level': 'DEBUG',
            'propagate': False,
        },
        # 'another_module': {
        #     'handlers': ['console', 'file_warning'],
        #     'level': 'WARNING',
        #     'propagate': False,
        # },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
            'level': 'INFO',
        },
        'file_info': {
            'class': 'logging.FileHandler',
            'filename': PATHS.output / 'build.log',
            'formatter': 'standard',
            'level': 'INFO',
        },
        'file_debug': {
            'class': 'logging.FileHandler',
            'filename': PATHS.output / 'debug.log',
            'formatter': 'detailed',
            'level': 'DEBUG',
        },
        # 'file_error': {
        #     'class': 'logging.FileHandler',
        #     'filename': PATHS.output / 'error.log',
        #     'formatter': 'detailed',
        #     'level': 'ERROR',
        # },
    },
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s - %(message)s',
            'datefmt': '%y-%m-%d %H:%M:%S',
        },
        'detailed': {
            'format': '%(asctime)s [%(levelname)s] %(name)s:%(lineno)s, in %(funcName)s - %(message)s',
            'datefmt': '%y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '%(levelname)s - %(message)s',
            'datefmt': '%H:%M:%S',
        },
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
log = logging.getLogger(__name__)


# %% helper funcs
def format_log_msg(text: list[str]):
    return '\n\t' + '\n\t'.join(text)


def find_file(file_name: str):
    """
    Attempt to find a single file within the project directory
    """
    file_paths = list(PATHS.project.rglob(file_name))
    if len(file_paths) == 0:
        msg = f'No "{file_name}" file detected in the repo.'
        log.error(msg)
        raise FileNotFoundError(msg)
    elif len(file_paths) > 1:
        printable_file_paths = [str(fp.relative_to(PATHS.project.parent)) 
                                for fp in file_paths]
        msg = format_log_msg(
            [f'Multiple files named "{file_name}" detected in the repo:',
             f'\t- {"\n\t\t- ".join(printable_file_paths)}',
             'Please use unique file names.'])
        log.error(msg)
        raise RuntimeError(msg)
    else:
        file_path, = file_paths
        return file_path


# def find_file(file_name: str):
#     """
#     Attempt to find a single file within the project directory
#     """
#     try:
#         file_paths = list(PATHS.project.rglob(file_name))
#         file_path, = file_paths
#     except ValueError:
#         if len(file_paths) == 0:
#             msg = f'No "{file_name}" file detected in the repo.'
#             log.exception(msg)
#             raise FileNotFoundError(msg)
#         elif len(file_paths) > 1:
#             printable_file_paths = [str(fp.relative_to(PATHS.project.parent)) 
#                                     for fp in file_paths]
#             msg = format_log_msg(
#                 [f'Multiple files named "{file_name}" detected in the repo:',
#                  f'\t- {"\n\t\t- ".join(printable_file_paths)}',
#                  'Please use unique file names.'])
#             log.exception(msg)
#             raise RuntimeError(msg)
#     return file_path

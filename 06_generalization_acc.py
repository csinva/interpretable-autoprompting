from model_utils import prompt_classification
import data
import numpy as np
import torch
from torch import nn
import matplotlib.pyplot as plt
from copy import deepcopy
import pandas as pd
from tqdm import tqdm
from collections import defaultdict
from transformers import AutoTokenizer
import pandas as pd
import seaborn as sns
from types import SimpleNamespace
from datasets import Dataset
from os.path import join as oj
import pickle as pkl
import os
import dvu
dvu.set_style()
from os.path import dirname
repo_dir = dirname(os.path.abspath(__file__))
results_acc_dir = oj(repo_dir, 'results', 'generalization_acc')
os.makedirs(results_acc_dir, exist_ok=True)




task_names_math_one = ['square_one', 'exp_one', 'double_one', 'fibonacci_one']
task_names_math_two = ['add_two', 'multiply_two', 'divide_two', 'subtract_two',
                       'max_two', 'first_two']
task_names_math_three = ['add_three', 'multiply_three', 'max_three', 'first_three']
task_names_anli = ['task1146_country_capital', 'task1509_evalution_antonyms', 'task1147_country_currency',
                   'task1149_item_check_edible', 'task183_rhyme_generation', 'task1191_food_veg_nonveg',
                   'task092_check_prime_classification', 'task088_identify_typo_verification',
                   'task1336_peixian_equity_evaluation_corpus_gender_classifier', 'task107_splash_question_to_sql'
                   ]


######################## ACTUAL HYPERPARAMS ################################
checkpoints_test = ['EleutherAI/gpt-neo-2.7B', 'EleutherAI/gpt-j-6B']
TASK_SETTINGS = {
    'one_digit_all': {
        'task_names': task_names_math_one + task_names_math_two + task_names_anli,
        'max_digit': 10,
        'n_shots': [1, 5, 10],
        'prompt_types': ['', 'manual'],
    },
    'double_digit_two_nums': {
        'task_names': task_names_math_two,
        'max_digit': 100,
        'n_shots': [1, 5, 10],
        'prompt_types': ['', 'manual'],
    },
    'one_digit_three_nums': {
        'task_names': task_names_math_three,
        'max_digit': 10,
        'n_shots': [1, 5, 10],
        'prompt_types': ['', 'manual'],
    },
    'sweep_in_distr_math': {
        'task_names': task_names_math_one + task_names_math_two, # + task_names_anli,
        'max_digit': 10,
        'n_shots': [1],
        'prompt_types': ['autoprompt', 'iprompt', '', 'manual'],  # ['', 'manual'],
    },
    'sweep_double_digit_math': {
        'task_names': task_names_math_two,
        'max_digit': 100,
        'n_shots': [1],
        'prompt_types': ['autoprompt', 'iprompt', '', 'manual'], 
    },
    'sweep_one_digit_three_nums_math': {
        'task_names': task_names_math_three,
        'max_digit': 10,
        'n_shots': [1],
        'prompt_types': ['autoprompt', 'iprompt', '', 'manual'], 
    },
}
task_key = 'sweep_one_digit_three_nums_math'
############################################################################


# prepare the args
batch_sizes = {
    'gpt2-medium': 32,
    'EleutherAI/gpt-j-6B': 8,
    'EleutherAI/gpt-neo-2.7B': 16,
    'EleutherAI/gpt-neox-20b': 1,
}


class fake_args:
    template_num_task_phrasing = 0
    max_dset_size = 1000
    max_digit = 10
    seed = 1
    train_split_frac = 0.75

    # these will be varied
    n_shots = 1
    task_name = 'add_two'


args = fake_args()
np.random.seed(args.seed)
settings = TASK_SETTINGS[task_key]
args.max_digit = settings['max_digit']
prompts_saved = pkl.load(open(oj(results_acc_dir, 'prompts.pkl'), 'rb'))

for checkpoint in checkpoints_test:
    d = defaultdict(list)
    print('loading', checkpoint)
    model = prompt_classification.create_model(checkpoint)
    print('calculating accs...')
    for task_name in tqdm(settings['task_names']):
        for n_shots in settings['n_shots']:
            for prompt_type in settings['prompt_types']:
                args.task_name = task_name
                args.n_shots = n_shots
                (dset, dset_test), check_answer_func, descr = data.get_data(
                    args, args.task_name, n_shots=args.n_shots,
                    train_split_frac=args.train_split_frac
                )
                d['checkpoint'].append(checkpoint)
                d['prompt'].append(prompt_type)
                d['task_name'].append(task_name)
                d['n_shots'].append(n_shots)
                d['max_digit'].append(args.max_digit)
                if prompt_type == 'manual':
                    prompt_actual = descr
                elif prompt_type in ['autoprompt', 'iprompt']:
                    # get saved prompt
                    prompt_actual = prompts_saved.loc[task_name][prompt_type]
                elif prompt_type == '':
                    prompt_actual = prompt_type

                d['prompt_actual'].append(prompt_actual)
                batch_size = batch_sizes.get(checkpoint, 16)
                if task_name == 'task107_splash_question_to_sql':
                    batch_size = max(1, batch_size//4)
                loss, acc = prompt_classification.test_model_on_task_with_prefix(
                    dset=dset, model=model, prefix=prompt_actual, multi_token=True, verbose=False,
                    batch_size=batch_size,
                )
                d['acc'].append(acc)

            baseline_acc_dir = oj(results_acc_dir, 'baseline_accs')
            ckpt = checkpoint.replace("/", "___")
            if task_key == 'one_digit_all':
                save_name = oj(baseline_acc_dir, f'baseline_accs_{ckpt}___nshots={n_shots}.pkl')
            elif task_key == 'double_digit_two_nums':
                save_name = oj(baseline_acc_dir, f'baseline_accs_{ckpt}___nshots={n_shots}___maxdigit={args.max_digit}.pkl')
            elif task_key == 'one_digit_three_nums':
                save_name = oj(baseline_acc_dir, f'baseline_accs_{ckpt}___nshots={n_shots}___three_nums.pkl')
            elif task_key.startswith('sweep'):
                save_name = oj(results_acc_dir, f'accs_sweep/accs_{ckpt}__{task_key}__{task_name}__prompt_type={prompt_type}.pkl')
            pkl.dump(d, open(save_name, 'wb'))

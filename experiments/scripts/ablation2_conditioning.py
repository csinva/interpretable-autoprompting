import itertools
import os
from os.path import dirname
import sys
import submit_utils
repo_dir = dirname(dirname(os.path.abspath(__file__)))

# save_dir = '/home/jxm3/research/prompting/interpretable-autoprompting/results_icml/ablation2'
save_dir = '/home/jxm3/research/prompting/interpretable-autoprompting/results_icml/ablation2_rerun'

cmd_python = 'python'

PARAMS_SHARED_DICT = {
    # things to average over
    'seed': [1, 2, 3, 4],

    # things to vary
    'task_name_list': [
        # MATH
        'subtract_two',
        'add_two', 'multiply_two', 
        'max_two', 'first_two',
        'square_one', 'double_one',
        'exp_one',  'fibonacci_one',
        'divide_two', 
        # ANLI
        'task1146_country_capital',
        'task1147_country_currency',
        'task1509_evalution_antonyms',
        'task1149_item_check_edible',
        'task183_rhyme_generation',
        'task1191_food_veg_nonveg',
        'task092_check_prime_classification',
        'task088_identify_typo_verification',
        'task1336_peixian_equity_evaluation_corpus_gender_classifier',
        'task107_splash_question_to_sql'
    ],
    'model_cls': ['iprompt'],
    'num_learned_tokens': [6],

    # stopping criteria
    'max_dset_size': [10 * 16],
    'max_n_datapoints': [10 * 16],
    'early_stopping_steps': [50],

    # fixed params
    'max_digit': [10],
    'max_length': [64],
    'train_split_frac': [1.0],
    'single_shot_loss': [1],
    'iprompt_pop_size': [4],
    'iprompt_num_random_generations': [4],
    'iprompt_num_mutations': [2],
    ##
    'iprompt_conditioning_strategy': ['\"\"'],
    'single_shot_loss': [1],
    'n_shots': [5],
    ##
    # 'iprompt_conditioning_strategy': ['\"\"', 'x_only', 'y_only', 'unconditional'],
    # 'single_shot_loss': [1],
    # 'n_shots': [5],
    ##
    # 'iprompt_conditioning_strategy': ['\"\"'],
    # 'single_shot_loss': [0],
    # 'n_shots': [5],
    ## 
    # 'iprompt_conditioning_strategy': ['\"\"'],
    # 'single_shot_loss': [1],
    # 'n_shots': [1],
}
PARAMS_SHARED_DICT['save_dir'] = [save_dir]
PARAMS_COUPLED_DICT = { 
    ('checkpoint', 'batch_size', 'float16'): [
        ('EleutherAI/gpt-j-6B', 16, 1),
    ],
}

ks_final, param_combos_final = submit_utils.combine_param_dicts(
    PARAMS_SHARED_DICT, PARAMS_COUPLED_DICT)

print('running job')
submit_utils.run_dicts(
    ks_final, param_combos_final, cmd_python=cmd_python,
    script_name='03_train_prefix.py', actually_run=True,
    use_slurm=True, save_dir=save_dir, slurm_gpu_str='gpu:1',
)

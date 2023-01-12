from typing import Dict, List, Tuple

import datasets
import functools
import os
import random
import string
import numpy as np
import time
import torch
import transformers
import argparse
from transformers import AutoTokenizer, AutoModel, AutoModelForCausalLM, AutoModelForSeq2SeqLM
from tqdm import tqdm
from collections import defaultdict
from iprompt.prefix import (
    AutoPrompt, iPrompt,
    PrefixLoss, PrefixModel,
    PromptTunedModel, HotFlip, GumbelPrefixModel
)
import pandas as pd
import iprompt.data as data
import logging
import pickle as pkl
from torch.utils.data import DataLoader
from datetime import datetime


device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


model_cls_dict = {
    'autoprompt': AutoPrompt,
    'genetic': iPrompt,  # outdated alias
    'iprompt': iPrompt,
    'gumbel': GumbelPrefixModel,
    'hotflip': HotFlip,
    'prompt_tune': PromptTunedModel,
}


def train_model(
    args: argparse.Namespace,
    r: Dict[str, List],
    dset: datasets.Dataset,
    model: PrefixModel,
    tokenizer: transformers.PreTrainedTokenizer
):
    """
    Trains a model, either by optimizing continuous embeddings or finding an optimal discrete embedding.

    Params
    ------
    r: dict
        dictionary of things to save
    """

    r['train_start_time'] = time.time()
    model.train()

    model = model.to(device)
    dataloader = DataLoader(
        dset, batch_size=args.batch_size, shuffle=True, drop_last=False)

    # optimizer
    optim = torch.optim.AdamW(model.trainable_params, lr=args.lr)

    assert model.training

    # Compute loss only over possible answers to make task easier
    possible_answer_ids = []
    for batch in dataloader:
        y_text = [answer for answer in batch['output']]
        y_tokenized = tokenizer(y_text, return_tensors='pt', padding='longest')
        # only test on the single next token
        true_next_token_ids = y_tokenized['input_ids'][:, 0]
        possible_answer_ids.extend(true_next_token_ids.tolist())

    possible_answer_ids = torch.tensor(possible_answer_ids)
    num_unique_answers = len(set(possible_answer_ids.tolist()))
    assert num_unique_answers > 0, "need multiple answers for multiple choice"
    random_acc = 1 / num_unique_answers * 100.0
    majority_count = (
        possible_answer_ids[:, None] == possible_answer_ids[None, :]).sum(dim=1).max()
    majority_acc = majority_count * 100.0 / len(possible_answer_ids)
    print(
        f"Training with {num_unique_answers} possible answers / random acc {random_acc:.1f}% / majority acc {majority_acc:.1f}%")

    vocab_size = len(tokenizer.vocab)

    if args.mask_possible_answers:
        possible_answer_mask = (
            torch.arange(start=0, end=vocab_size)[:, None]
            ==
            possible_answer_ids[None, :]
        ).any(dim=1).to(device)
    else:
        possible_answer_mask = None

    stopping_early = False
    total_n_steps = 0
    total_n_datapoints = 0
    for epoch in range(args.n_epochs):
        model.pre_epoch()

        all_losses = []

        total_n = 0
        total_n_correct = 0
        pbar = tqdm(enumerate(dataloader), total=len(dataloader))
        for idx, batch in pbar:
            total_n_steps += 1
            if (args.n_shots > 1) and (args.single_shot_loss):
                batch['input'] = batch['last_input']
            x_text, y_text = model.prepare_batch(batch=batch)

            tok = functools.partial(
                model.tokenizer, return_tensors='pt', padding='longest',
                truncation=True, max_length=args.max_length)
            x_tokenized = tok(x_text).to(device)
            y_tokenized = tok(y_text).to(device)
            full_text_tokenized = tok(batch['text']).to(device)

            loss, n_correct = model.compute_loss_and_call_backward(
                x_tokenized=x_tokenized,
                y_tokenized=y_tokenized,
                possible_answer_mask=possible_answer_mask,
                full_text_tokenized=full_text_tokenized,
            )

            r["all_losses"].append(loss)
            r["all_n_correct"].append(n_correct)

            total_n += len(x_text)
            total_n_datapoints += len(x_text)
            total_n_correct += n_correct

            all_losses.append(loss)
            pbar.set_description(f"Loss = {loss:.3f}")

            if not args.accum_grad_over_epoch:
                # if hotflip, autoprompt, etc., grad will be zero
                optim.step()
                optim.zero_grad()

            # Early stopping, check after step
            model_check_early_stop = model.check_early_stop()
            if model_check_early_stop:
                print("model_check_early_stop returned true")
            if (total_n_datapoints >= args.max_n_datapoints) or (total_n_steps >= args.max_n_steps) or model_check_early_stop:
                stopping_early = True
                break

        if stopping_early:
            print(f"Ending epoch {epoch} early...")
        avg_loss = sum(all_losses) / len(all_losses)
        print(f"Epoch {epoch}. average loss = {avg_loss:.3f} / {total_n_correct} / {total_n} correct ({total_n_correct/total_n*100:.2f}%)")

        # save stuff
        for key, val in model.compute_metrics().items():
            r[key].append(val)

        # r['losses'].append(avg_loss)
        if epoch % args.epoch_save_interval == 0:
            os.makedirs(save_dir, exist_ok=True)
            pkl.dump(r, open(os.path.join(save_dir, 'results.pkl'), 'wb'))

        model.post_epoch(dataloader=dataloader,
                         possible_answer_mask=possible_answer_mask)

        if args.accum_grad_over_epoch:
            optim.step()
            optim.zero_grad()

        # Early stopping, check after epoch
        if stopping_early:
            print(
                f"Stopping early after {total_n_steps} steps and {total_n_datapoints} datapoints")
            break

    # Serialize model-specific stuff (prefixes & losses for autoprompt, embeddings for prompt tuning, etc.)
    n_eval = 128
    eval_dset = datasets.Dataset.from_dict(dset[:n_eval])
    eval_dataloader = DataLoader(
        eval_dset, batch_size=args.batch_size, shuffle=True, drop_last=False)
    r.update(model.serialize(eval_dataloader, possible_answer_mask))

    # save whether prefixes fit the template
    if "prefixes" in r:
        r["prefixes__check_answer_func"] = list(
            map(check_answer_func, r["prefixes"]))

    r['train_end_time'] = time.time()
    r['train_time_elapsed'] = r['train_end_time'] - r['train_start_time']

    pkl.dump(r, open(os.path.join(save_dir, 'results.pkl'), 'wb'))

    print("finished training")
    return r


def eval_model_with_set_prefix(
    args: argparse.Namespace,
    r: Dict[str, List],
    dataloader: DataLoader,
    model: PrefixModel,
    tokenizer: transformers.PreTrainedTokenizer
) -> Tuple[float, float]:
    """
    Evaluates a model based on set prefix. May be called multiple times with different prefixes

    Params
    ------
    r: dict
        dictionary of things to save

    Returns: Tuple[float, float]
        average loss, accuracy per sample over eval dataset
    """
    pbar = tqdm(enumerate(dataloader), total=len(dataloader),
                desc='evaluating data', colour='red', leave=False)
    total_loss = 0.0
    total_n = 0
    total_n_correct = 0
    for idx, batch in pbar:
        x_text, y_text = model.prepare_batch(batch=batch)

        tok = functools.partial(
            model.tokenizer, return_tensors='pt', padding='longest')
        x_tokenized = tok(x_text).to(device)
        y_tokenized = tok(y_text).to(device)
        full_text_tokenized = tok(batch['text']).to(device)

        with torch.no_grad():
            _input_ids, loss, n_correct = model._compute_loss_with_set_prefix(
                original_input_ids=x_tokenized.input_ids,
                next_token_ids=y_tokenized.input_ids,
                possible_answer_mask=None,  # TODO: implement eval verbalizer
                prefix_ids=None,
            )

        total_loss += loss.item()
        total_n += len(x_text)
        total_n_correct += n_correct

        pbar.set_description(
            f"Acc = {total_n_correct}/{total_n} {(total_n_correct/total_n*100):.2f}%")


    return (total_loss / total_n), (total_n_correct / total_n)


def eval_model(
    args: argparse.Namespace,
    r: Dict[str, List],
    dset: datasets.Dataset,
    model: PrefixModel,
    tokenizer: transformers.PreTrainedTokenizer
):
    """
    Evaluates a model based on the learned prefix(es).

    Params
    ------
    r: dict
        dictionary of things to save
    """
    r["test_start_time"] = time.time()
    model.eval()
    dataloader = DataLoader(
        dset, batch_size=args.batch_size, shuffle=False, drop_last=False)

    if r["prefixes"]:
        # if we specified multiple prefixes (autoprompt or genetic), let's evaluate them all!
        for prefix_ids in tqdm(r["prefix_ids"], desc="evaluating prefixes"):
            model._set_prefix_ids(new_ids=torch.tensor(prefix_ids).to(device))

            loss, acc = eval_model_with_set_prefix(
                args=args, r=r, dataloader=dataloader, model=model, tokenizer=tokenizer
            )

            r["prefix_test_loss"].append(loss)
            r["prefix_test_acc"].append(acc)
        r["num_prefixes_used_for_test"] = len(r["prefixes"])

    else:
        # otherwise, there's just one prefix (like for prompt tuning) so just run single eval loop.
        loss, acc = eval_model_with_set_prefix(
            args=args, r=r, dataloader=dataloader, model=model, tokenizer=tokenizer
        )
        r["prefix_test_acc"] = loss
        r["prefix_test_loss"] = acc
        r["num_prefixes_used_for_test"] = 1

    r["test_end_time"] = time.time()
    r["test_time_elapsed"] = r["test_end_time"] - r["test_start_time"]
    pkl.dump(r, open(os.path.join(save_dir, 'results.pkl'), 'wb'))
    return r


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument('--model_cls', type=str,
                        choices=model_cls_dict.keys(),
                        required=True,
                        help='model type to use for training')
    parser.add_argument('--batch_size', type=int, default=500,
                        help='batch size for training')
    parser.add_argument('--seed', type=int, default=1,
                        help='random seed')
    parser.add_argument('--n_epochs', type=int, default=100,
                        help='number of epochs for training')
    parser.add_argument('--max_n_steps', type=int, default=10**10,
                        help='max number of steps for training')
    parser.add_argument('--max_n_datapoints', type=int, default=10**10,
                        help='max number of datapoints for training')
    parser.add_argument('--train_split_frac', type=float,
                        default=None, help='fraction for train-test split if desired')
    parser.add_argument('--max_dset_size', type=int,
                        default=10**4, help='maximum allowable dataset size')
    parser.add_argument('--early_stopping_steps', type=int, default=-1,
                        help='if > 0, number of steps until stopping early after no improvement')
    parser.add_argument('--max_digit', type=int, default=100,
                        help='maximum value of each digit in summand')
    parser.add_argument('--template_num_init_string', type=int, default=0,
                        help='the number of the manually-specified prefix to be initialize with')
    parser.add_argument('--template_num_task_phrasing', type=int, default=0,
                        help='the number of the manual template for any given task (number of options varies with task')
    parser.add_argument('--save_dir', type=str, default='results',
                        help='directory for saving')
    parser.add_argument('--epoch_save_interval', type=int, default=1,
                        help='interval to save results')
    parser.add_argument('--iprompt_generation_repetition_penalty', type=float, default=2.0,
                        help='repetition penalty for iprompt generations')
    parser.add_argument('--lr', type=float, default=1e-4,
                        help='learning rate')
    parser.add_argument('--gamma', type=float, default=0.0,
                        help='hparam: weight for language modeling loss')
    parser.add_argument('--task_name', type=str, default='add_two',
                        choices=(data.TASKS.keys() - {'SUFFIX'}),
                        help='name of task')
    parser.add_argument('--task_name_list', nargs="*", default=None,
                        help='names of tasks as list; alternative to passing task_name')
    parser.add_argument('--n_shots', type=int, default=1,
                        help='number of shots in the prompt')
    parser.add_argument('--autoprompt_init_strategy', type=str, default='the',
                        choices=('random', 'the'), help='initialization strategy for discrete tokens')
    parser.add_argument('--max_length', type=int, default=128,
                        help='maximum length for inputs')
    parser.add_argument('--single_shot_loss', type=int, default=0,
                        help='if n_shots==0, load multiple shots but only use one compute loss')
    parser.add_argument('--mask_possible_answers', type=int, default=0,
                        help='only compute loss over possible answer tokens')
    parser.add_argument('--hotflip_num_candidates', type=int, default=10,
                        help='number of candidates to rerank, for hotflip')
    parser.add_argument('--accum_grad_over_epoch', type=int, default=0, choices=(0, 1),
                        help='should we clear gradients after a batch, or only at the end of the epoch?')
    parser.add_argument('--num_learned_tokens', type=int, default=1,
                        help='number of learned prefix tokens (for gumbel, hotflip, autoprompt, prompt-tuning)')
    parser.add_argument('--use_preprefix', type=int, default=0, choices=(0, 1),
                        help='whether to use a template pre-prefix')
    parser.add_argument('--iprompt_preprefix_str', type=str, default='',
                        help='Text like "Output the number that" or "Answer F/M if"... \
                            If this is passed, automatically use_preprifx'
                        )
    parser.add_argument('--iprompt_pop_size', type=int, default=8)
    parser.add_argument('--iprompt_num_mutations', type=int, default=4)
    parser.add_argument('--iprompt_generation_temp', type=float, default=1.0)
    parser.add_argument('--iprompt_generation_top_p', type=float, default=1.0)
    parser.add_argument('--iprompt_num_random_generations',
                        type=int, default=4)
    parser.add_argument('--llm_float16', '--float16', '--parsimonious', type=int, default=0, choices=(0, 1),
                        help='if true, loads LLM in fp16 and at low-ram')
    parser.add_argument('--checkpoint', type=str, default="EleutherAI/gpt-neo-2.7B",
                        choices=(
                            ############################
                            "EleutherAI/gpt-neo-125M",
                            "EleutherAI/gpt-neo-1.3B",
                            "EleutherAI/gpt-neo-2.7B",
                            ############################
                            "EleutherAI/gpt-j-6B",
                            ############################
                            "EleutherAI/gpt-neox-20b",
                            ############################
                            "gpt2",        # 117M params
                            "gpt2-medium",  # 355M params
                            "gpt2-large",  # 774M params
                            "gpt2-xl",     # 1.5B params
                            ############################
                            "google/flan-t5-small",  # 80M Params
                            "google/flan-t5-base",   # 250M Params
                            "google/flan-t5-large",  # 780M Params
                            "google/flan-t5-xl",     # 3B Params
                            "google/flan-t5-xxl",    # 11B Params
                            ############################
                            "facebook/galactica-125m",
                            "facebook/galactica-1.3b",
                            "facebook/galactica-6.7b",
                        ),
                        help='model checkpoint to use'
                        )
    args = parser.parse_args()

    random_str = ''.join(random.choices(string.ascii_lowercase, k=12))
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    transformers.set_seed(args.seed)

    args.use_generic_query = 0

    if (args.mask_possible_answers) and (args.train_split_frac is not None):
        print("Warning: mask possible answers not supported for eval")

    # iterate over tasks
    if args.task_name_list is not None:
        logging.info('using task_name_list ' + str(args.task_name_list))
    else:
        args.task_name_list = [args.task_name]
    for task_idx, task_name in enumerate(args.task_name_list):
        print(f'*** Executing task {task_idx+1}/{len(args.task_name_list)}')
        # actually set the task
        args.task_name = task_name

        r = defaultdict(list)
        r.update(vars(args))
        logger = logging.getLogger()
        logging.basicConfig(level=logging.INFO)

        logger.info('loading model and data...')
        checkpoint = args.checkpoint
        tokenizer = AutoTokenizer.from_pretrained(checkpoint)
        tokenizer.eos_token = tokenizer.eos_token or 0
        tokenizer.pad_token = tokenizer.eos_token

        llm_cls = AutoModelForSeq2SeqLM if 't5' in checkpoint else AutoModelForCausalLM

        if args.llm_float16:
            if checkpoint == "EleutherAI/gpt-j-6B":
                lm = llm_cls.from_pretrained(
                    checkpoint, output_hidden_states=False, pad_token_id=tokenizer.eos_token_id,
                    revision="float16", torch_dtype=torch.float16, low_cpu_mem_usage=True
                )
            else:
                # (only certain models are pre-float16ed)
                print(f"trying to convert {checkpoint} to float16...")
                lm = llm_cls.from_pretrained(
                    checkpoint, torch_dtype=torch.float16
                )
                lm = lm.half()
        else:
            lm = llm_cls.from_pretrained(
                checkpoint, output_hidden_states=False, pad_token_id=tokenizer.eos_token_id
            )
        loss_func = PrefixLoss(gamma=args.gamma, tokenizer=tokenizer)

        # set up saving
        save_dir_unique = datetime.now().strftime("%b_%d_%H_%M_") + \
            random_str
        save_dir = os.path.join(args.save_dir, save_dir_unique)
        logging.info('saving to ' + save_dir)
        args.save_dir_unique = save_dir

        preprefix = ''
        if args.use_preprefix or not args.iprompt_preprefix_str == '':
            if args.iprompt_preprefix_str == '':
                preprefix = data.get_init_suffix(
                    args.task_name, args.use_generic_query, args.template_num_init_string)
            else:
                preprefix = args.iprompt_preprefix_str

        print(f'preprefix: `{preprefix}`')
        model = model_cls_dict[args.model_cls](
            args=args,
            loss_func=loss_func,
            model=lm,
            tokenizer=tokenizer,
            preprefix=preprefix
        )
        dset, check_answer_func, description = data.get_data(
            task_name=args.task_name, n_shots=args.n_shots, train_split_frac=args.train_split_frac, max_dset_size=args.max_dset_size,
            template_num_task_phrasing=args.template_num_task_phrasing, max_digit=args.max_digit
        )
        print(f'Attempting task with description: "{description}"')

        logger.info('beginning training...')

        if args.train_split_frac is None:
            r = train_model(args=args, r=r, dset=dset,
                            model=model, tokenizer=tokenizer)
        else:
            dset_train, dset_test = dset
            r = train_model(args=args, r=r, dset=dset_train,
                            model=model, tokenizer=tokenizer)
            # r = eval_model(args=args, r=r, dset=Dataset.from_dict(dset_test[:128]), model=model, tokenizer=tokenizer)

import logging
import string

import numpy as np
import torch

import iprompt.data as data
import iprompt.parallel as parallel
import iprompt.utils as utils


device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


def get_stopwords():
    """Leave this import here in case we don't want to install nltk
    """
    import nltk
    nltk.download('stopwords')
    from nltk.corpus import stopwords
    return set(stopwords.words('english'))


def get_next_token_logits(ex_inputs, ex_inputs_str, model, possible_answer_mask):
    """Gets logits for the next token given inputs with appropriate attention mask
    """
    from iprompt.prompt_classification import Gpt3Model
    # print('model type:', type(model))
    if isinstance(model, Gpt3Model):
        return model.get_logits(
            x_text=ex_inputs_str,
            possible_answer_mask=possible_answer_mask
        ).squeeze(dim=1)
    else:
        # go through model
        outputs = model.model(
            input_ids=ex_inputs['input_ids'], attention_mask=ex_inputs['attention_mask'])
        logits = outputs['logits']  # (batch_size, seq_len, vocab_size)

        # get positions of the next-token hidden state
        positions_next_token = ex_inputs['attention_mask'].cumsum(dim=1).argmax(dim=1)

        # index at correct positions
        batch_size = logits.shape[0]
        next_token_logits = logits[torch.arange(batch_size).to(logits.device), positions_next_token]

        # import transformers
        # tt = transformers.AutoTokenizer.from_pretrained(model.model.name_or_path)
        # import pdb; pdb.set_trace()

        # optionally take a mask over some tokens
        next_token_logits = torch.where(
            possible_answer_mask, next_token_logits, torch.tensor(
                float('-inf')).to(device)
        )

        return next_token_logits


def get_probs_avg_next_token(args, suffix_str: str, model, dataloader,
                             tokenizer, use_softmax=True):
    """Get the average probs for the next token across the entire dataset
    Actually returns logits not probs in case of overflow issues
    """
    num_examples = 0
    cum_logits = None
    for idx, batch in enumerate(dataloader):

        # set up inputs
        text = batch['text']
        full_text = [text[i] + suffix_str
                     for i in range(len(text))]
        ex_inputs = tokenizer(
            full_text, padding='longest', return_tensors='pt')
        ex_inputs = parallel.inputs_to_device(args, ex_inputs)

        # actually get next-token logits
        next_token_logits = get_next_token_logits(ex_inputs, model)

        # apply softmax
        if use_softmax:
            next_token_logits = next_token_logits.softmax(axis=-1)

        # sum over batch-size
        next_token_logits = next_token_logits.sum(axis=0)

        # take log softmax
        # next_token_logits = next_token_logits.log_softmax(dim=-1)

        # accumulate logits
        if cum_logits is None:
            cum_logits = next_token_logits.detach()
        else:
            cum_logits += next_token_logits.detach()
        num_examples += len(text)

    # use averaged logits
    avg_logits = cum_logits / num_examples
    avg_logits = avg_logits.detach().cpu().numpy().squeeze()

    # convert to probs (TODO: make this less likely to overflow)
    # avg_probs = np.exp(avg_logits)  # softmax part 1
    # avg_probs /= np.sum(avg_probs)  # softmax part 2
    return avg_logits


def get_probs_single_query_next_token(args, suffix_str: str, model, dataloader, tokenizer):
    """Get the probs for the next token for a single example.
    Kind of hacky - just takes the first example from the first batch
    """
    # get a single input from the first batch and ignore the rest
    batch = next(iter(dataloader))
    text = batch['text']
    full_text = [text[0] + suffix_str]
    ex_inputs = tokenizer(
        full_text, padding='longest', return_tensors='pt')
    ex_inputs = parallel.inputs_to_device(args, ex_inputs)

    # actually get next-token logits
    next_token_logits = get_next_token_logits(ex_inputs, model)

    # convert to make it more usable
    next_token_logits = (
        next_token_logits
        .sum(axis=0)  # sum over batch_size
        .log_softmax(dim=-1)
        .detach()
        .cpu()
        .numpy()
        .squeeze()
    )

    return next_token_logits

def get_top_candidates_and_probs_suff(r, min_len=4):
    probs = np.array(r['running_prob'])
    suffix_str_added = np.array(r['suffix_str_added'])
    num_tokens_added = np.array(r['num_tokens_added'])
    idxs = (num_tokens_added == max(r['num_tokens_added']))

    suffix_str_added = suffix_str_added[idxs]
    probs = probs[idxs]


    args_prob_sort = np.argsort(probs)[::-1]
    suffix_str_added = suffix_str_added[args_prob_sort]
    probs = probs[args_prob_sort]
    return suffix_str_added, probs

def train_suffix(args, r, model, dataloader, check_answer_func, tokenizer, save_dir,
                 disallow_whitespace_tokens=False,
                 beam_size_printing=1000,  # this might slow things down a bit but won't change anything
                 beam_size_for_saving=15
                 ):
    """Here we find the suffix which maximizes the likelihood over all examples.
    The algorithm is basically to do breadth-first beam-search on the next-token prob distr. averaged over all examples
    """

    # set up BFS beam search
    suffix_str = data.get_init_suffix(args.task_name, args.use_generic_query, args.template_num_init_string)

    suffixes = [{'s': suffix_str, 'num_tokens_added': 0,
                 'running_prob': 1, 'num_suffixes_checked': 0}]
    r['suffix_str_init'] = suffix_str
    r['len_suffix_str_init'] = len(suffix_str)
    num_model_queries = 0
    logging.info(
        f'num batches: {len(dataloader)} batch_size {args.batch_size}')
    if not args.use_stopwords:
        STOPWORDS = get_stopwords()

    while len(suffixes) > 0:
        suffix_dict = suffixes.pop()
        suffix_str = suffix_dict['s']
        num_suffixes_checked = suffix_dict['num_suffixes_checked']

        # save results for suffix_str
        r['suffix_str_added'].append(suffix_str[r['len_suffix_str_init']:])
        r['num_tokens_added'].append(suffix_dict['num_tokens_added'])
        r['num_model_queries'].append(num_model_queries)
        r['running_prob'].append(suffix_dict['running_prob'])
        
        # break if we've added enough tokens
        if suffix_dict['num_tokens_added'] >= args.max_num_tokens:
            continue

        # get avg_probs
        if args.use_single_query:
            avg_probs = get_probs_single_query_next_token(
                args, suffix_str, model, dataloader, tokenizer)
        else:
            avg_probs = get_probs_avg_next_token(
                args, suffix_str, model, dataloader, tokenizer)
        num_model_queries += 1

        # could also check out top_k_top_p_filtering
        # (https://huggingface.co/docs/transformers/v4.16.2/en/task_summary)
        # get the topk indexes and tokens
        top_k_inds = np.arange(avg_probs.size)
        # top_k_inds = np.argpartition(avg_probs, -beam_size_for_saving)# [-beam_size_printing:]  # get topk (hardcoded as 500)

        # sort the topk (largest first)
        top_k_inds = top_k_inds[np.argsort(avg_probs[top_k_inds])][::-1]
        top_decoded_tokens = np.array(
            [tokenizer.decode(ind) for ind in top_k_inds])

        # disallow bad tokens
        if disallow_whitespace_tokens:
            disallowed_idxs = np.array([s.isspace() or all(c in string.punctuation for c in s)
                                       for s in top_decoded_tokens], dtype=bool)
            top_k_inds = top_k_inds[~disallowed_idxs]
            top_decoded_tokens = top_decoded_tokens[~disallowed_idxs]
        if not args.use_stopwords:
            disallowed_idxs = np.array([s.lower().strip() in STOPWORDS
                                       for s in top_decoded_tokens], dtype=bool)
            top_k_inds = top_k_inds[~disallowed_idxs]
            top_decoded_tokens = top_decoded_tokens[~disallowed_idxs]

        # logging
        logging.info(str(num_model_queries) + ' ' + repr(suffix_str))
        for i in range(beam_size_printing):
            logging.debug(
                '\t ' + repr(top_decoded_tokens[i]) + '\t' + f'{avg_probs[top_k_inds[i]]:.2E}')
        logging.debug('\t' + 'idxs_correct: ' + str(np.argwhere(
            [check_answer_func(x) for x in top_decoded_tokens]).flatten().tolist()))

        # if we made it here, we did not find the answer and early stop
        if args.use_verbose_saving:
            r['correct'].append(False)
            r['suffix_str_full'].append(suffix_str)
            r['decoded_token'].append(top_decoded_tokens[0])
            r['top_decoded_tokens_dict'].append({
                top_decoded_tokens[i]: avg_probs[top_k_inds[i]]
                for i in range(beam_size_for_saving)
            })

        # find answer rank (only if we're at the first token)
        # in the best case, it is at position 0 (most likely completion)
        if suffix_dict['num_tokens_added'] == 0:
            pos_correct = np.array(
                list(map(check_answer_func, top_decoded_tokens)))
            r['final_answer_pos_initial_token'] = np.where(pos_correct)[
                0].min()
        utils.save(args, save_dir, r, epoch=None, final=True)

        # check larger than args.beam_size in case the answer was basically right there
        for beam_num in range(args.beam_size + args.beam_size_extra):
            suffix_new = suffix_str + top_decoded_tokens[beam_num]
            if check_answer_func(suffix_new):
                # save the first answer we find
                if not 'final_answer_full' in r.keys():
                    r['final_answer_full'] = suffix_new
                    r['final_answer_added'] = suffix_new[r['len_suffix_str_init']:]
                    r['final_model_queries'] = num_model_queries
                    r['final_num_suffixes_checked'] = num_suffixes_checked + \
                        beam_num + 1
                    r['final_answer_depth'] = suffix_dict['num_tokens_added'] + 1
                    logging.info('successful early stopping :)')
                    logging.info('\t' + repr(r['suffix_str_init']))
                    logging.info('\t' + repr(r['final_answer_added']))
                    logging.info('\t' + 'pos_initial_token: ' +
                                 repr(r['final_answer_pos_initial_token']))
                    logging.info(save_dir)
                    utils.save(args, save_dir, r, final=True)
                    utils.save_json(r={  # save some key outputs in readable form
                        k: r[k]
                        for k in r if isinstance(r[k], str) or isinstance(r[k], int)
                    }, save_dir=save_dir, fname='final.json')

                # usually we just return after finding the answer
                if args.use_early_stopping:
                    return

            # for bfs insert at beginning (dfs would append at end)
            if beam_num < args.beam_size:
                suffixes.insert(0, {
                    's': suffix_new,
                    'num_tokens_added': suffix_dict['num_tokens_added'] + 1,
                    'running_prob': suffix_dict['running_prob'] * avg_probs[top_k_inds[beam_num]],

                    # checked beam_size at current suffix + all suffixes before this one (assumes BFS-beam search)
                    # this is the total number of suffixes checked at the time when this will be opened above
                    'num_suffixes_checked': num_suffixes_checked + (args.beam_size + args.beam_size_extra) * (beam_num + 1)
                })

    if args.max_num_tokens > 1:
        candidates, probs = get_top_candidates_and_probs_suff(r)    
        r['top_prompt'] = candidates[0]
        r['top_candidates'] = candidates
        r['top_probs'] = probs

    # failed to find anything (if early stopping), save and return
    logging.info('failed early stopping :/')
    logging.info('\t' + 'pos_initial_token: ' +
                 repr(r['final_answer_pos_initial_token']))
    utils.save(args, save_dir, r, final=True)
    print(r)

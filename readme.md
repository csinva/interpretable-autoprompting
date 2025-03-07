<h1 align="center">  Interpretable autoprompting </h1>
<p align="center"> Natural language explanations of a <i>dataset</i> via language-model autoprompting.
</p>

<p align="center">
  <a href="http://csinva.io/imodelsX/iprompt/api.html#imodelsx.iprompt.api.explain_dataset_iprompt">📚 sklearn-friendly api</a> •
  <a href="https://github.com/csinva/interpretable-autoprompting/blob/master/demo.ipynb">📖 demo notebook</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-mit-blue.svg">
  <img src="https://img.shields.io/badge/python-3.6+-blue">
  <img src="https://img.shields.io/pypi/v/imodelsx?color=green">
</p>  


<b>Official code for using / reproducing iPrompt from the paper "Explaining Patterns in Data  with  Language Models via Interpretable Autoprompting" (<a href="https://arxiv.org/abs/2210.01848">Singh*, Morris*, Aneja, Rush, & Gao, 2022</a>) </b> iPrompt generates a human-interpretable prompt that explains patterns in data while still inducing strong generalization performance.

https://user-images.githubusercontent.com/4960970/197355573-e5a1af4c-0784-4344-a314-79793f284b97.mov

# Quickstart
**Installation**: `pip install imodelsx` (or, for more control, clone and install from source)

**Usage example** (see [imodelsX](https://github.com/csinva/imodelsX) for more details):

```python
from imodelsx import explain_dataset_iprompt, get_add_two_numbers_dataset

# get a simple dataset of adding two numbers
input_strings, output_strings = get_add_two_numbers_dataset(num_examples=100)
for i in range(5):
    print(repr(input_strings[i]), repr(output_strings[i]))

# explain the relationship between the inputs and outputs
# with a natural-language prompt string
prompts, metadata = explain_dataset_iprompt(
    input_strings=input_strings,
    output_strings=output_strings,
    checkpoint='EleutherAI/gpt-j-6B', # which language model to use
    num_learned_tokens=3, # how long of a prompt to learn
    n_shots=3, # shots per example

    n_epochs=15, # how many epochs to search
    verbose=0, # how much to print
    llm_float16=True, # whether to load the model in float_16
)
--------
prompts is a list of found natural-language prompt strings
```

# Docs
<blockquote>
<b>Abstract</b>: Large language models (LLMs) have displayed an impressive ability to harness natural language to perform complex tasks. In this work, we explore whether we can leverage this learned ability to find and explain patterns in data. Specifically, given a pre-trained LLM and data examples, we introduce interpretable autoprompting (iPrompt), an algorithm that generates a natural-language string explaining the data. iPrompt iteratively alternates between generating explanations with an LLM and reranking them based on their performance when used as a prompt. Experiments on a wide range of datasets, from synthetic mathematics to natural-language understanding, show that iPrompt can yield meaningful insights by accurately finding groundtruth dataset descriptions. Moreover, the prompts produced by iPrompt are simultaneously human-interpretable and highly effective for generalization: on real-world sentiment classification datasets, iPrompt produces prompts that match or even improve upon human-written prompts for GPT-3. Finally, experiments with an fMRI dataset show the potential for iPrompt to aid in scientific discovery.
</blockquote>

- the main api requires simply importing `imodelsx`
- the `experiments` and `experiments/scripts` folders contain hyperparameters for running sweeps contained in the paper
  - note: args that start with `use_` are boolean
- the `notebooks` folder contains notebooks for analyzing the outputs + making figures

# Related work

- **fMRI data experiment**: Uses scientific data/code from https://github.com/HuthLab/speechmodeltutorial linked to the paper "Natural speech reveals the semantic maps that tile human cerebral cortex" [Huth, A. G. et al., (2016) _Nature_.](https://www.nature.com/articles/nature17637)
- AutoPrompt: find an (uninterpretable) prompt using input-gradients ([paper](https://arxiv.org/abs/2010.15980); [github](https://github.com/ucinlp/autoprompt))
- Aug-imodels: Explain a dataset by fitting an interpretable linear model/decision tree leveraging a pre-trained language model ([paper](https://arxiv.org/abs/2209.11799); [github](https://github.com/csinva/emb-gam))

## Testing
- to check if the pipeline seems to work, install pytest then run `pytest` from the repo's root directory

If this package is useful for you, please cite the following!

```r
@article{singh2022iprompt,
  title = {Explaining Patterns in Data with Language Models via Interpretable Autoprompting},
  author = {Singh, Chandan and Morris, John X. and Aneja, Jyoti and Rush, Alexander M. and Gao, Jianfeng},
  year = {2022},
  url = {https://arxiv.org/abs/2210.01848},
  publisher = {arXiv},  
  doi = {10.48550/ARXIV.2210.01848}  
}
```

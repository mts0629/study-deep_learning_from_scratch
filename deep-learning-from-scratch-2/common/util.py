import sys
sys.path.append("..")

import os
from common.np import *


def preprocess(text):
    """ Create a simple corpus and word-id dictionaries from the text
    """
    text = text.lower()
    text = text.replace(".", " .")
    words = text.split(" ")

    word_to_id = {}
    id_to_word = {}
    for word in words:
        if word not in word_to_id:
            new_id = len(word_to_id)
            word_to_id[word] = new_id
            id_to_word[new_id] = word

    corpus = np.array([word_to_id[w] for w in words])

    return corpus, word_to_id, id_to_word


def cos_similarity(x, y, eps=1e-8):
    """ Calculate cosine similarity between 2 word vectors
    """
    nx = x / np.sqrt(np.sum(x**2) + eps)
    ny = y / np.sqrt(np.sum(y**2) + eps)
    return np.dot(nx, ny)


def most_similar(query, word_to_id, id_to_word, word_matrix, top=5):
    """ Find top N words which are similar to the query, from a word matrix
    """
    if query not in word_to_id:
        print(f"{query} is not found")
        return

    print(f"\n[query] {query}")
    query_id = word_to_id[query]
    query_vec = word_matrix[query_id]

    # Calculate cosine simiralities between all of the other word vectors
    vocab_size = len(id_to_word)
    similarity = np.zeros(vocab_size)
    for i in range(vocab_size):
        similarity[i] = cos_similarity(word_matrix[i], query_vec)

    # Print top N words and the simiralities
    count = 0
    for i in (-1 * similarity).argsort():  # (* -1) to sort with descending order
        if id_to_word[i] == query:
            continue
        print(f" {id_to_word[i]}: {similarity[i]}")

        count += 1
        if count >= top:
            return


def convert_one_hot(corpus, vocab_size):
    """ Convert word ids to one-hot vectors
    """
    N = corpus.shape[0]

    if corpus.ndim == 1:  # CBOW target
        one_hot = np.zeros((N, vocab_size), dtype=np.int32)
        for idx, word_id in enumerate(corpus):
            one_hot[idx, word_id] = 1
    elif corpus.ndim == 2:  # CBOW context
        C = corpus.shape[1]
        one_hot = np.zeros((N, C, vocab_size), dtype=np.int32)
        for idx_0, word_ids in enumerate(corpus):
            for idx_1, word_id in enumerate(word_ids):
                one_hot[idx_0, idx_1, word_id] = 1

    return one_hot


def create_co_matrix(corpus, vocab_size, window_size=1):
    """ Create a co-occurrence matrix from the corpus
    """
    corpus_size = len(corpus)
    co_matrix = np.zeros((vocab_size, vocab_size), dtype=np.int32)

    for idx, word_id in enumerate(corpus):
        for i in range(1, window_size+1):
            left_idx = idx - i
            right_idx = idx + i

            if left_idx >= 0:
                left_word_id = corpus[left_idx]
                co_matrix[word_id, left_word_id] += 1

            if right_idx < corpus_size:
                right_word_id = corpus[right_idx]
                co_matrix[word_id, right_word_id] += 1

    return co_matrix


def ppmi(C, verbose=False, eps=1e-8):
    """ Calculate the PPMI (Positive Pointwise Mutual Information) matrix
    """
    M = np.zeros_like(C, dtype=np.float32)  # PPMI matrix
    N = np.sum(C)  # Number of words in the corpus
    S = np.sum(C, axis=0)  # Number of each word in the corpus
    total = C.shape[0] * C.shape[1]
    cnt = 0

    for i in range(C.shape[0]):
        for j in range(C.shape[1]):
            pmi = np.log2(C[i, j] * N / (S[j] * S[i]) + eps)
            M[i, j] = max(0, pmi)

            if verbose:
                # Show the progress
                cnt += 1
                if cnt % (total//100 + 1) == 0:
                    print(f"{100*cnt/total}% done")

    return M


def create_contexts_target(corpus, window_size=1):
    """ Create contexts and targets from the corpus
    """
    target = corpus[window_size:-window_size]
    contexts = []

    for idx in range(window_size, len(corpus)-window_size):
        cs = []
        for t in range(-window_size, window_size+1):
            if t == 0:
                continue
            cs.append(corpus[idx + t])
        contexts.append(cs)

    return np.array(contexts), np.array(target)


def to_cpu(x):
    """ Convert cupy.ndarray to numpy.ndarray
    """
    import numpy
    if type(x) == numpy.ndarray:
        return x
    return np.asnumpy(x)


def to_gpu(x):
    """ Convert numpy.ndarray to cupy.ndarray
    """
    import cupy
    if type(x) == cupy.ndarray:
        return x
    return cupy.asarray(x)


def clip_grads(grads, max_norm):
    """ Gradients clipping with the specified L2 norm
    """
    total_norm = 0
    for grad in grads:
        total_norm += np.sum(grad ** 2)
    total_norm = np.sqrt(total_norm)

    rate = max_norm / (total_norm + 1e-6)
    if rate < 1:
        for grad in grads:
            grad *= rate


def eval_perplexity(model, corpus, batch_size=10, time_size=35):
    """ Evaluate perplexity
    """
    print("evaluating perplexity ...")
    corpus_size = len(corpus)
    total_loss = 0
    max_iters = (corpus_size - 1) // (batch_size * time_size)
    jump = (corpus_size - 1) // batch_size

    for iters in range(max_iters):
        xs = np.zeros((batch_size, time_size), dtype=np.int32)
        ts = np.zeros((batch_size, time_size), dtype=np.int32)
        time_offset = iters * time_size
        offsets = [time_offset + (i * jump) for i in range(batch_size)]
        for t in range(time_size):
            for i, offset in enumerate(offsets):
                xs[i, t] = corpus[(offset + t) % corpus_size]
                ts[i, t] = corpus[(offset + t + 1) % corpus_size]

        try:
            loss = model.forward(xs, ts, train_flg=False)
        except TypeError:
            loss = model.forward(xs, ts)
        total_loss += loss

        sys.stdout.write("\r%d / %d" % (iters, max_iters))
        sys.stdout.flush()

    print("")
    ppl = np.exp(total_loss / max_iters)
    return ppl


def eval_seq2seq(model, question, correct, id_to_char,
                 verbose=False, is_reverse=False):
    """ Evaluate Seq2seq model
    """
    correct = correct.flatten()

    # The first delimiter
    start_id = correct[0]
    correct = correct[1:]
    guess = model.generate(question, start_id, len(correct))

    # Convert to a string
    question = "".join([id_to_char[int(c)] for c in question.flatten()])
    correct = "".join([id_to_char[int(c)] for c in correct])
    guess = "".join([id_to_char[int(c)] for c in guess])

    if verbose:
        if is_reverse:
            question = question[::-1]

        colors = {"ok": "\033[92m", "fail": "\033[91m", "close": "\033[0m"}
        print("Q", question)
        print("T", correct)

        is_windows = os.name = "nt"

        # Print a guess with coloring
        if correct == guess:
            mark = colors['ok'] + '☑' + colors['close']
            if is_windows:
                mark = "O"
            print(mark + " " + guess)
        else:
            mark = colors['fail'] + '☒' + colors['close']
            if is_windows:
                mark = "X"
            print(mark + " " + guess)
        print("---")

    return 1 if guess == correct else 0


def analogy(a, b, c, word_to_id, id_to_word, word_matrix, top=5, answer=None):
    """ Analogy task with using ditributed representation
    """
    for word in (a, b, c):
        if word not in word_to_id:
            print(f"{word} in not found")
            return

    print(f"\n[analogy] {a}:{b} = {c}:?")
    a_vec, b_vec, c_vec = \
        word_matrix[word_to_id[a]], \
        word_matrix[word_to_id[b]], \
        word_matrix[word_to_id[c]]

    # Get a query vector: vec(b) - vec(a) + vec(c)
    query_vec = b_vec - a_vec + c_vec
    query_vec = normalize(query_vec)

    # Calculate similarities
    similarity = np.dot(word_matrix, query_vec)

    if answer is not None:
        print("===> {} : {}".format(
            answer,
            str(np.dot(word_matrix[word_to_id[answer]], query_vec))
        ))

    # Sort the similarities by descending order
    count = 0
    for i in (-1 * similarity).argsort():
        if np.isnan(similarity[i]):
            continue
        if id_to_word[i] in (a, b, c):
            continue
        print(f" {id_to_word[i]}: {similarity[i]}")

        count += 1
        if count >= top:
            return


def normalize(x):
    """ Normalize the input
    """
    if x.ndim == 2:
        s = np.sqrt((x * x).sum(1))
        x /= s.reshape((s.shape[0], 1))
    elif x.ndim == 1:
        s = np.sqrt((x * x).sum())
        x /= s
    return x

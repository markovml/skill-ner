# process_n_gram, process_uni_gram : filter and score

# native packs
import collections
import functools
import math

# installed packs
import numpy as np
import jellyfish
# my packs
from skillNer.text_class import Text
from skillNer.general_params import TOKEN_DIST
from scipy.sparse import csr_matrix
import pandas as pd

class Utils:
    def __init__(self, nlp, skills_db):
        self.nlp = nlp
        self.skills_db = skills_db
        self.token_dist = TOKEN_DIST
        self.sign = functools.partial(math.copysign, 1)
        return

    def make_one(self, cluster, len_):
        a = [1] * len_
        return [1*(i in cluster) for i, one in enumerate(a)]

    def split_at_values(self, lst, val):
        return [i for i, x in enumerate(lst) if x != val]

    def grouper(self, iterable, dist):
        prev = None
        group = []
        for item in iterable:
            if prev == None or item - prev <= dist:
                group.append(item)
            else:
                yield group
                group = [item]
            prev = item
        if group:
            yield group

    def get_clusters(self, co_oc):
        clusters = []
        i=0
        for row in co_oc.tolil().rows:
            if row != []:

            # divide row into clusters deivided by 0 : 0 occurence where clusters refer to token id
            # example [2,3,0,5,0,0] -> [0,1,3] -> [[0,1],[3]]
            # clusts = list(self.grouper(self.split_at_values(row, 0), 1))
                clusts = list(self.grouper(row, 1))
                # select token relative cluster via its idx
               # example i==0 => [[0,1],[3]] => a = [0,1]
                a = [c for c in clusts if i in c][0]
                if a not in clusters:
                    clusters.append(a)
            i+=1        
        # return unique clusters [token id]
        return clusters

    def get_corpus(self, text, matches):
        """create a corpus matrix which will be used in future computations.

           Parameters
           ----------
           matches (list): list of matches generated by sub matchers
           text (Text): text object

           Returns
           -------

               corpus : return binary matrix   => (n :skills matched )* (m : tokens in text )  
                                                1 : skill contains token
                                                0 : otherwise
               look_up : return a mapper from skill_ids to its equivalent row index in corpus
        """
        
        len_ = len(text)
        unique_skills = list(set([match['skill_id'] for match in matches]))
        skill_text_match_bin = [0]*len_
        match_df = pd.DataFrame(matches)
        match_df_group = match_df.groupby('skill_id')['doc_node_id']
        corpus=[]
        look_up = {}
        idx=0
        on_inds =[]
        for skill_id,g in match_df_group:
            skill_text_match_bin = [0]*len_
            look_up[idx]=skill_id
            on_inds = [j for sub in g for j in sub]
            skill_text_match_bin_updated = [(i in on_inds)*1 for i, _ in enumerate(skill_text_match_bin)]

            corpus.append(skill_text_match_bin_updated)
            idx+=1
        # return csr_matrix(corpus), lookup
        return np.array(corpus), look_up

    def one_gram_sim(self, text_str, skill_str):
        # transform into sentence
        text = text_str + ' ' + skill_str
        tokens = self.nlp(text)
        token1, token2 = tokens[0], tokens[1]
        try:
            vec_similarity = token1.similarity(token2)
            return vec_similarity
        except:
            # try Levenshtein Distance  if words not found in spacy corpus
            str_distance_similarity = jellyfish.jaro_distance(
                text_str.lower(), skill_str.lower())
            return str_distance_similarity

    def compute_w_ratio(self, skill_id, matched_tokens):
        skill_name = self.skills_db[skill_id]['high_surfce_forms']['full'].split(
            ' ')
        skill_len = self.skills_db[skill_id]['skill_len']
        # favorize the matched tokens uphead
        late_match_penalty_coef = 0.1
        token_ids = sum([(1-late_match_penalty_coef*skill_name.index(token))
                         for token in matched_tokens])

        return token_ids/skill_len

    def retain(self, text_obj, span, skill_id, sk_look, corpus):
        """ add doc here  """
        real_id, type_ = sk_look[skill_id].split('_')

        # get skill len
        len_ = self.skills_db[real_id]['skill_len']
        # get intersection length of full  skill name  and span tokens
        len_condition = corpus[skill_id].dot(span)

        # start :to be deleted
        s_gr = np.array(list(span))*np.array(list(corpus[skill_id]))
        def condition(x): return x == 1

        s_gr_n = [idx for idx, element in enumerate(
            s_gr) if condition(element)]
        # end

        if type_ == 'oneToken':
            # if skill is n_gram (n>2)
            score = self.compute_w_ratio(
                real_id, [text_obj[ind].lemmed for ind in s_gr_n])

        if type_ == 'fullUni':
            score = 1

        if type_ == 'lowSurf':
            if len_ > 1:

                score = sum(s_gr)

            else:
                # if skill is uni_gram (n=1)
                text_str = ' '.join([str(text_obj[i])
                                     for i, val in enumerate(s_gr) if val == 1])
                skill_str = self.skills_db[real_id]['high_surfce_forms']['full']

                score = self.one_gram_sim(text_str, skill_str)

        return {'skill_id': real_id,
                'doc_node_id':  [i for i, val in enumerate(s_gr) if val == 1],
                'doc_node_value': ' '.join([str(text_obj[i]) for i, val in enumerate(s_gr) if val == 1]),
                'type': type_,
                'score': score,
                'len': len_condition
                }
    # main functions

    def process_n_gram(self, matches, text_obj: Text):
        """apply on conflicted matches to choose which  ones to keep

           Parameters
           ----------
           matches (list): list of matches generated by sub matchers
           text_obj (Text): text object 

           Returns
           -------

               list: return choosen skills with their given words span in the text and thir score  

           """
        if len(matches) == 0:
            return matches

        text_tokens = text_obj.lemmed(as_list=True)
        len_ = len(text_tokens)

        corpus, look_up = self.get_corpus(text_tokens, matches)
        corpus_csr = csr_matrix(corpus)
        # generate spans (a span is a list of tokens where one or more skills are matched)

        # co-occurence of tokens aij : co-occurence count of token i with token j
        # co_occ = np.matmul(corpus.T, corpus)
        co_occ_csr = corpus_csr.T.dot(corpus_csr)
        # create spans of tokens that co-occured
        clusters = self.get_clusters(co_occ_csr)

        # one hot encoding of clusters
        # example [0,1,2] => [1,1,1,0,0,0] , encoding length  = text length
        ones = [self.make_one(cluster, len_) for cluster in clusters]
        # generate list of span and list of skills that have conflict on spans [(span,[skill_id])]
        spans_conflicts = [(np.array(one), np.array([a_[0] for a_ in np.argwhere(corpus_csr.dot(one) != 0)]))
                           for one in ones]

        # filter and score
        new_spans = []
        for span_conflict in spans_conflicts:
            span, skill_ids = span_conflict
            span_scored_skills = []
            types = []
            scores = []
            lens = []
            for sk_id in skill_ids:
                # score skill given span
                scored_sk_obj = self.retain(
                    text_obj, span, sk_id, look_up, corpus)
                span_scored_skills.append(scored_sk_obj)
                types.append(scored_sk_obj['type'])
                lens.append(scored_sk_obj['len'])
                scores.append(scored_sk_obj['score'])
            # extract best candiate for a given span
            if 'oneToken' in types and len(set(types)) > 1:
                # having a ngram skill with other types in span condiates :
                # priotize skills with high match length if length >1
                id_ = np.array(scores).argmax()
                max_score = 0.5  # selection treshold
                for i, len_ in enumerate(lens):
                    if len_ > 1 and types[i] == 'oneToken':
                        if scores[i] >= max_score:
                            id_ = i

                new_spans.append(span_scored_skills[id_])

            else:
                max_score_index = np.array(scores).argmax()
                new_spans.append(span_scored_skills[max_score_index])

        return new_spans

# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
###############################################################################

"""
_hf_cvt.py: HuggingFace Tokenizer/Processor Converter
"""
import os
import json
import onnx
from numpy import array as nparray
from functools import partial
from collections import namedtuple, OrderedDict

from .util import read_file
from ._cuops import CustomOpConverter, SingleOpGraph


class HFTokenizerConverter(CustomOpConverter):
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    @staticmethod
    def convert_bpe_vocab(hf_tokenizer):
        attrs = {'vocab': json.dumps(
            hf_tokenizer.encoder, separators=(',', ':'))}
        if hf_tokenizer.added_tokens_encoder:
            token_map = [f"{_k}={_v}" for _k,
                         _v in hf_tokenizer.added_tokens_encoder.items()]
            attrs.update({"added_token": "\n".join(token_map)})

        sorted_merges = {v_: k_ for k_, v_ in hf_tokenizer.bpe_ranks.items()}
        attrs['merges'] = '\n'.join("{} {}".format(
            *sorted_merges[n_]) for n_ in range(len(sorted_merges)))
        return attrs

    @staticmethod
    def convert_json_vocab(hf_tokenizer):
        filenames = getattr(hf_tokenizer, "vocab_files_names", None)
        if filenames is None:
            raise ValueError(
                f"{hf_tokenizer.__name__}: vocab_files_names is not found")

        tokenizer_file = filenames["tokenizer_file"]
        vocab_file = getattr(hf_tokenizer, "vocab_file", None)
        if (vocab_file is None) or (not os.path.exists(vocab_file)):
            model_dir = hf_tokenizer.name_or_path
        else:
            model_dir = os.path.dirname(vocab_file)
        f = open(os.path.join(model_dir, tokenizer_file), "r", encoding="utf-8")
        tokenizer_json = json.load(f)
        f.close()
        # get vocab object from json file
        vocab = tokenizer_json.get("model", {}).get("vocab", {})
        sorted_merges = tokenizer_json.get("model", {}).get("merges", [])
        sorted_merges = [v_.replace("\n", "<0x0A>") for v_ in sorted_merges]
        attrs = {"vocab": json.dumps(vocab, separators=(",", ":"))}
        attrs["merges"] = "\n".join(sorted_merges)
        if hf_tokenizer.added_tokens_encoder:
            token_map = [f"{_k}={_v}" for _k,
                         _v in hf_tokenizer.added_tokens_encoder.items()]
            attrs.update({"added_token": "\n".join(token_map)})

        return attrs

    @staticmethod
    def get_model_name(hf_tokenizer):
        name = hf_tokenizer.__class__.__name__
        if name.endswith("Fast"):
            name = name[: -len("Fast")]
        if name.endswith("Tokenizer"):
            name = name[: -len("Tokenizer")]
        return name

    def bpe_tokenizer(self, **kwargs):
        hf_bpe_tokenizer = self.tokenizer
        if getattr(hf_bpe_tokenizer, "is_fast", True):
            attrs = self.convert_json_vocab(hf_bpe_tokenizer)
        else:
            attrs = self.convert_bpe_vocab(hf_bpe_tokenizer)

        attrs.update({"model_name": self.get_model_name(hf_bpe_tokenizer)})
        attrs.update(**kwargs)
        return attrs

    def bert_tokenizer(self, **kwargs):
        hf_bert_tokenizer = self.tokenizer
        # has to be sorted since the id of token was generated automatically.
        ordered_vocab = OrderedDict(
            sorted(hf_bert_tokenizer.vocab.items(), key=lambda item: int(item[1])))
        vocab = '\n'.join(ordered_vocab.keys())
        attrs = dict(vocab=vocab)
        init_kwargs = hf_bert_tokenizer.init_kwargs
        attrs['do_lower_case'] = 1 if 'do_lower_case' in init_kwargs and init_kwargs.get(
            'do_lower_case') else 0
        attrs['strip_accents'] = 1 if 'strip_accents' in init_kwargs and init_kwargs.get(
            'strip_accents') else 0
        attrs.update(**kwargs)
        return attrs

    def bert_decoder(self, **kwargs):
        hf_bert_tokenizer = self.tokenizer
        attrs = {'vocab': json.dumps(
            hf_bert_tokenizer.ids_to_tokens, separators=(',', ':'))}
        attrs.update(**kwargs)
        return attrs

    def bpe_decoder(self, **kwargs):
        decoder = self.tokenizer.decoder
        # if decoder is not iterable, build it from the vocab.
        if not hasattr(decoder, "__iter__"):
            decoder = {id: token for token, id in self.tokenizer.vocab.items()}
        id_vocab = "\n".join([decoder[_idx] for _idx in sorted(decoder)])
        byte_decoder = getattr(self.tokenizer, "byte_decoder", None)
        if byte_decoder is None:
            # let's take it as a SPM tokenizer
            byte_decoder = {chr(0x2581): ord(' ')}
        str_byte_decoder = "\n".join(
            ["{}\t{}".format(ord(_c), str(byte_decoder[_c])) for _c in byte_decoder])
        all_special_ids = self.tokenizer.all_special_ids
        added_tokens = self.tokenizer.added_tokens_decoder
        str_all_special_ids = "\n".join([str(_id) for _id in all_special_ids])
        str_added_tokens = "\n".join(
            ["{}\t{}".format(str(_id), added_tokens[_id]) for _id in added_tokens])
        kwargs.update({
            "id_vocab": id_vocab,
            "byte_decoder": str_byte_decoder,
            "added_tokens": str_added_tokens,
            "all_special_ids": str_all_special_ids,
            "skip_special_tokens": kwargs.get("skip_special_tokens", False)
        })
        return kwargs

    def clip_tokenizer(self, **kwargs):
        hf_clip_tokenizer = self.tokenizer

        if type(self.tokenizer).__name__.endswith('Fast'):
            raise ValueError(
                'Please use the slow version of the tokenizer (ex: CLIPTokenizer).')

        attrs = self.convert_bpe_vocab(hf_clip_tokenizer)
        attrs.update(**kwargs)
        return attrs

    def roberta_tokenizer(self, **kwargs):
        hf_roberta_tokenizer = self.tokenizer

        if type(self.tokenizer).__name__.endswith('Fast'):
            raise ValueError(
                'Please use the slow version of the tokenizer (ex: RobertaTokenizer).')

        attrs = self.convert_bpe_vocab(hf_roberta_tokenizer)
        attrs.update(**kwargs)
        return attrs

    def spm_tokenizer(self, **kwargs):
        attrs = {'model': read_file(self.tokenizer.vocab_file, 'rb')}
        attrs.update(**kwargs)
        return attrs

    def spm_decoder(self, **kwargs):
        attrs = {'model': read_file(self.tokenizer.vocab_file, 'rb')}
        attrs.update(**kwargs)
        return attrs


TokenOpParam = namedtuple("TokenOpParam",
                          ["pre_op", "pre_attribute_cvt",
                           "post_op", "post_attribute_cvt",
                           "default_encoder_inputs",
                           "default_decoder_inputs"],
                          defaults=(None, None, None, None, None))

# Some tokenizers can be added by this table
# https://github.com/huggingface/transformers/blob/main/src/transformers/convert_slow_tokenizer.py#L1252
# @formatter:off
_PROCESSOR_DICT = {
    "BertTokenizer":        TokenOpParam('BertTokenizer',   HFTokenizerConverter.bert_tokenizer,
                                         'BertDecoder',     HFTokenizerConverter.bpe_decoder, None, None),
    "DistilBertTokenizer":  TokenOpParam('BertTokenizer',   HFTokenizerConverter.bert_tokenizer,
                                         'BertDecoder',     HFTokenizerConverter.bpe_decoder, None, None),
    "GPT2Tokenizer":        TokenOpParam('GPT2Tokenizer',   HFTokenizerConverter.bpe_tokenizer,
                                         'BpeDecoder',      HFTokenizerConverter.bpe_decoder, None, None),
    "CodeGenTokenizer":     TokenOpParam('GPT2Tokenizer',   HFTokenizerConverter.bpe_tokenizer,
                                         'BpeDecoder',      HFTokenizerConverter.bpe_decoder, None, None),
    "CLIPTokenizer":        TokenOpParam('CLIPTokenizer',   HFTokenizerConverter.clip_tokenizer,
                                         'BpeDecoder',      HFTokenizerConverter.bpe_decoder, None, None),
    "RobertaTokenizer":     TokenOpParam('RobertaTokenizer',        HFTokenizerConverter.roberta_tokenizer,
                                         'BpeDecoder',              HFTokenizerConverter.bpe_decoder, None, None),
    "BartTokenizer":        TokenOpParam('RobertaTokenizer',        HFTokenizerConverter.roberta_tokenizer,
                                         'BpeDecoder',              HFTokenizerConverter.bpe_decoder, None, None),
    "LayoutLMv3Tokenizer":  TokenOpParam('RobertaTokenizer',        HFTokenizerConverter.roberta_tokenizer,
                                         'BpeDecoder',              HFTokenizerConverter.bpe_decoder, None, None),
    "LongformerTokenizer":  TokenOpParam('RobertaTokenizer',        HFTokenizerConverter.roberta_tokenizer,
                                         'BpeDecoder',              HFTokenizerConverter.bpe_decoder, None, None),
    "LEDTokenizer":         TokenOpParam('RobertaTokenizer',        HFTokenizerConverter.roberta_tokenizer,
                                         'BpeDecoder',              HFTokenizerConverter.bpe_decoder, None, None),
    "MvpTokenizer":         TokenOpParam('RobertaTokenizer',        HFTokenizerConverter.roberta_tokenizer,
                                         'BpeDecoder',              HFTokenizerConverter.bpe_decoder, None, None),
    "T5Tokenizer":          TokenOpParam('SentencepieceTokenizer',  HFTokenizerConverter.spm_tokenizer,
                                         'SentencepieceDecoder',    HFTokenizerConverter.spm_decoder,
                                         default_encoder_inputs={'add_eos': [True]}, default_decoder_inputs=None),
    "LlamaTokenizer":       TokenOpParam('SpmTokenizer',            HFTokenizerConverter.bpe_tokenizer,
                                         'BpeDecoder',              HFTokenizerConverter.bpe_decoder, None, None),
    "XLMRobertaTokenizer":  TokenOpParam('SentencepieceTokenizer',  HFTokenizerConverter.spm_tokenizer,
                                         'SentencepieceDecoder',    HFTokenizerConverter.spm_decoder,
                                         default_encoder_inputs={'add_bos': [True], 'add_eos': [True], 'fairseq': [True]},
                                         default_decoder_inputs={'fairseq': [True]}),
}
# @formatter:on


class HFTokenizerOnnxGraph:

    @staticmethod
    def extract_cls_name(processor):
        cls_name = processor if isinstance(
            processor, str) else type(processor).__name__
        if cls_name.endswith("TokenizerFast"):
            cls_name = cls_name[:-len("Fast")]
        return cls_name

    @classmethod
    def is_supported(cls, processor):
        cls_name = cls.extract_cls_name(processor)
        return cls_name in _PROCESSOR_DICT

    def __init__(self, processor, **kwargs):
        cls_name = self.extract_cls_name(processor)
        self.cvt_quadruple = _PROCESSOR_DICT[cls_name]
        self.cvt_obj = HFTokenizerConverter(processor)

    def pre_processing(self, **kwargs):
        with_default_inputs = kwargs.pop("WITH_DEFAULT_INPUTS", True)
        cast_token_id = kwargs.pop("CAST_TOKEN_ID", False)

        _cvt_op = self.cvt_quadruple.pre_op
        _cvt_func = self.cvt_quadruple.pre_attribute_cvt
        cvt = partial(_cvt_func, self.cvt_obj)
        g = SingleOpGraph.build_graph(_cvt_op, cvt=cvt, **kwargs)
        default_inputs = []
        if with_default_inputs:
            op_class = SingleOpGraph.get_op_class(_cvt_op)
            default_inputs = op_class.input_default_values()
            if default_inputs is None:
                return g

        # add default_inputs into initializers to simplify the model input
        n_inputs = len(default_inputs)
        if self.cvt_quadruple.default_encoder_inputs is not None:
            default_inputs.update(self.cvt_quadruple.default_encoder_inputs)
            if len(default_inputs) != n_inputs:
                raise ValueError(
                    "Op: {} does not have the inputs from its TokenOpParam.".format(_cvt_op))

        new_initializers = []

        for k, v in default_inputs.items():
            input_value_info = next((i for i in g.input if i.name == k), None)
            if input_value_info is None:
                raise ValueError(
                    "The input {} is not found in the graph".format(k))

            np_dtype = onnx.helper.tensor_dtype_to_np_dtype(
                input_value_info.type.tensor_type.elem_type)
            value = nparray(v, np_dtype)
            new_initializers.append(onnx.numpy_helper.from_array(value, k))
        g.initializer.extend(new_initializers)
        new_inputs = [i for i in g.input if i.name not in default_inputs]
        g.ClearField("input")
        g.input.extend(new_inputs)

        if cast_token_id:
            # assume the first output is always the token ID.
            if g.output[0].type.tensor_type.elem_type != onnx.onnx_pb.TensorProto.INT64:
                new_output_name = g.output[0].name + '_cast'
                shape = g.output[0].type.tensor_type.shape
                cast_node = onnx.helper.make_node('Cast', [g.output[0].name], [new_output_name],
                                                  to=onnx.onnx_pb.TensorProto.INT64)
                new_output = [onnx.helper.make_tensor_value_info(
                    new_output_name, onnx.onnx_pb.TensorProto.INT64, None)] + list(g.output)[1:]
                if shape is not None:
                    new_output[0].type.tensor_type.shape.CopyFrom(shape)
                g.node.append(cast_node)
                g.ClearField('output')
                g.output.extend(new_output)

        return g

    def post_processing(self, **kwargs):
        with_default_inputs = kwargs.pop("WITH_DEFAULT_INPUTS", True)

        _cvt_op = self.cvt_quadruple.post_op
        _cvt_func = self.cvt_quadruple.post_attribute_cvt
        cvt = partial(_cvt_func, self.cvt_obj)
        g = SingleOpGraph.build_graph(_cvt_op, cvt=cvt, **kwargs)

        default_inputs = {}
        if with_default_inputs:
            op_class = SingleOpGraph.get_op_class(_cvt_op)
            default_inputs = op_class.input_default_values()
            if default_inputs is None:
                encoder_inputs = self.cvt_quadruple.default_encoder_inputs
                if encoder_inputs is not None and encoder_inputs["fairseq"]:
                    default_inputs = {} # need to set to empty dict to call .update later
                else:
                    return g

        # add default_inputs into initializers to simplify the model input
        if self.cvt_quadruple.default_decoder_inputs is not None:
            default_inputs.update(self.cvt_quadruple.default_decoder_inputs)

        new_initializers = []

        for k, v in default_inputs.items():
            input_value_info = next((i for i in g.input if i.name == k), None)
            if input_value_info is None:
                raise ValueError(
                    "The input {} is not found in the graph".format(k))

            np_dtype = onnx.helper.tensor_dtype_to_np_dtype(
                input_value_info.type.tensor_type.elem_type)
            value = nparray(v, np_dtype)
            new_initializers.append(onnx.numpy_helper.from_array(value, k))
        g.initializer.extend(new_initializers)
        new_inputs = [i for i in g.input if i.name not in default_inputs]
        g.ClearField("input")
        g.input.extend(new_inputs)

        return g

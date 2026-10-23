# rag_service.py
import os
import pickle
import numpy as np
import torch
from transformers import AutoTokenizer, AutoModel, AutoModelForCausalLM, BitsAndBytesConfig, StoppingCriteria, StoppingCriteriaList
from sentence_transformers import SentenceTransformer
import faiss

# ========= 配置路径 =========
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_CANDIDATES = [
    os.path.join(PROJECT_ROOT, "models", "llm", "qwen--Qwen2.5-7B-Instruct", "snapshots", "master"),
    os.path.join(PROJECT_ROOT, "models", "llm", "Qwen2.5-7B-Instruct", "snapshots", "master"),
    os.path.join(PROJECT_ROOT, "models", "models", "qwen--Qwen2.5-7B-Instruct", "snapshots", "master"),
]
MODEL_PATH = next((path for path in MODEL_CANDIDATES if os.path.exists(path)), MODEL_CANDIDATES[0])
VECTOR_DB_PATH = os.path.join(PROJECT_ROOT, "data", "vector_db")
EMBEDDING_MODEL_ROOT = os.path.join(PROJECT_ROOT, "models")

# 兼容旧目录名/旧路径，避免从历史项目复制后仍然残留硬编码
for legacy_path in (
    os.path.join(PROJECT_ROOT, "..", "LLM_Module"),
    os.path.join(PROJECT_ROOT, "..", "26挑战杯"),
):
    if os.path.exists(legacy_path):
        if not os.path.exists(MODEL_PATH):
            for candidate in MODEL_CANDIDATES:
                if os.path.exists(candidate):
                    MODEL_PATH = candidate
                    break
        if not os.path.exists(VECTOR_DB_PATH):
            VECTOR_DB_PATH = os.path.join(legacy_path, "data", "vector_db")
        if not os.path.exists(os.path.join(PROJECT_ROOT, "models")):
            EMBEDDING_MODEL_ROOT = os.path.join(legacy_path, "models")
        break

_embeddings = None
_vector_db = None
_texts = None
_sources = None
_tokenizer = None
_model = None

# ========= 停止生成标志 =========
_generation_stop_flag = False


class GenerationStopCriteria(StoppingCriteria):
    def __call__(self, input_ids, scores, **kwargs):
        return _generation_stop_flag


def stop_generation():
    """外部调用停止生成"""
    global _generation_stop_flag
    _generation_stop_flag = True
    print("⏹️ 收到停止生成信号")


def reset_generation_flag():
    """重置停止标志"""
    global _generation_stop_flag
    _generation_stop_flag = False


def find_embedding_model_path(root_path):
    candidate_roots = [
        os.path.join(PROJECT_ROOT, "models", "damo--nlp_corom_sentence-embedding_english-base"),
        os.path.join(PROJECT_ROOT, "models", "embedding_model"),
        os.path.join(PROJECT_ROOT, "models"),
        root_path,
    ]
    for path in candidate_roots:
        if not os.path.exists(path) or not os.path.isdir(path):
            continue
        for current_root, dirs, files in os.walk(path):
            if "config.json" in files and ("pytorch_model.bin" in files or "model.safetensors" in files or "tokenizer_config.json" in files):
                print(f"找到 Embedding 模型路径: {current_root}")
                return current_root
        if os.path.isdir(path):
            print(f"找到 Embedding 模型目录: {path}")
            return path
    default_path = os.path.join(PROJECT_ROOT, "models", "embedding_model")
    os.makedirs(default_path, exist_ok=True)
    print(f"创建默认 Embedding 模型目录: {default_path}")
    return default_path


class HuggingFaceEmbeddingsNoTrainer:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        local_path = find_embedding_model_path(EMBEDDING_MODEL_ROOT)
        if not os.path.exists(local_path) or not os.path.isdir(local_path):
            raise FileNotFoundError(f"未找到本地 Embedding 模型，请确认 {EMBEDDING_MODEL_ROOT} 下有完整的模型文件")

        def find_model_files(dir_path):
            for root, dirs, files in os.walk(dir_path):
                if any(f.endswith(('.bin', '.safetensors', '.onnx', '.pth')) for f in files):
                    return root
            return None

        model_dir = find_model_files(local_path)
        if model_dir:
            local_path = model_dir
            print(f"找到模型文件目录: {local_path}")
        else:
            print(f"⚠️ 警告: {local_path} 下没有找到模型文件，尝试使用 SentenceTransformer 下载默认模型...")
            try:
                self.model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
                self.tokenizer = None
                self.device = "cpu"
                print("✅ 使用 SentenceTransformer 加载默认模型")
                return
            except Exception as e:
                raise FileNotFoundError(f"本地模型文件缺失且无法下载默认模型: {e}")

        print(f"Using local embedding model: {local_path}")
        try:
            self.model = SentenceTransformer(local_path, device='cpu')
            self.tokenizer = None
            self.device = "cpu"
            print("✅ 使用 SentenceTransformer 加载本地模型成功")
        except Exception as e:
            print(f"SentenceTransformer 加载失败，尝试使用 AutoModel: {e}")
            self.tokenizer = AutoTokenizer.from_pretrained(local_path, local_files_only=True)
            self.model = AutoModel.from_pretrained(local_path, local_files_only=True)
            self.model.eval()
            self.device = "cpu"
            self.model = self.model.to(self.device)
            print("✅ 使用 AutoModel 加载本地模型成功")

    def embed_query(self, text):
        if hasattr(self.model, 'encode'):
            emb = self.model.encode(text, convert_to_numpy=True)
            return emb.flatten()
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=512, padding=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self.model(**inputs)
            token_embeddings = outputs.last_hidden_state
            attention_mask = inputs['attention_mask']
            mask = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
            sum_embeddings = (token_embeddings * mask).sum(dim=1)
            sum_mask = mask.sum(dim=1)
            emb = sum_embeddings / sum_mask
            emb = emb / torch.norm(emb, dim=1, keepdim=True)
            return emb.cpu().numpy().flatten()


def load_vector_db():
    global _texts, _sources
    vectors_path = os.path.join(VECTOR_DB_PATH, "vectors.npy")
    texts_path = os.path.join(VECTOR_DB_PATH, "texts.pkl")
    sources_path = os.path.join(VECTOR_DB_PATH, "sources.pkl")
    if not all(os.path.exists(p) for p in [vectors_path, texts_path, sources_path]):
        raise FileNotFoundError(f"向量库文件不完整，请运行 build_knowledge_base_v2.py 重新构建")
    vectors = np.load(vectors_path)
    with open(texts_path, "rb") as f:
        _texts = pickle.load(f)
    with open(sources_path, "rb") as f:
        _sources = pickle.load(f)
    dim = vectors.shape[1]
    index = faiss.IndexFlatL2(dim)
    index.add(vectors.astype(np.float32))
    return index


def init_rag():
    global _embeddings, _vector_db, _texts, _sources, _tokenizer, _model
    if _tokenizer is not None and _model is not None:
        return
    print("正在初始化 RAG 服务...")
    try:
        _embeddings = HuggingFaceEmbeddingsNoTrainer()
        print("✅ Embedding 模型加载成功！")
    except Exception as e:
        print("Embedding model loading failed:", e)
        return
    try:
        _vector_db = load_vector_db()
        print("✅ 知识库加载成功！")
    except Exception as e:
        print("Knowledge base loading failed:", e)
        _vector_db = None
        return
    try:
        print("正在加载 Qwen 模型（4-bit 量化模式）...")
        
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4"
        )
        
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_PATH,
            quantization_config=quantization_config,
            device_map="auto",
            trust_remote_code=True,
            torch_dtype=torch.float16
        )
        print("✅ Qwen 模型加载成功（4-bit 量化，运行在 GPU）！")
    except Exception as e:
        print("Qwen model loading failed:", e)
        print("尝试使用 CPU 模式加载...")
        try:
            _tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
            _model = AutoModelForCausalLM.from_pretrained(MODEL_PATH, trust_remote_code=True)
            _model = _model.to("cpu")
            print("✅ Qwen 模型加载成功（CPU 模式）！")
        except Exception as e2:
            print("Qwen model loading failed (CPU mode as well):", e2)
            _tokenizer = None
            _model = None
            return
    print("RAG service initialization completed!")


def rag_query(question, top_k=3, max_tokens=512):
    global _vector_db, _texts, _sources, _tokenizer, _model, _generation_stop_flag
    
    # 重置停止标志
    reset_generation_flag()
    
    print("Received question:", question[:50] + "...")
    
    if _tokenizer is None or _model is None:
        print("模型未初始化，尝试初始化...")
        init_rag()
    
    if _tokenizer is None or _model is None or _vector_db is None:
        print("RAG not ready")
        return {
            "answer": "⚠️ RAG 服务未就绪，请检查模型加载状态",
            "sources": [],
            "context": ""
        }
    
    try:
        # 检查点1 - 搜索前
        if _generation_stop_flag:
            print("⏹️ 生成被取消（搜索前）")
            return {"answer": "⏹️ 生成已取消", "sources": [], "context": ""}
        
        print("Searching knowledge base...")
        query_vec = _embeddings.embed_query(question)
        
        # 检查点2 - 向量化后
        if _generation_stop_flag:
            print("⏹️ 生成被取消（向量化后）")
            return {"answer": "⏹️ 生成已取消", "sources": [], "context": ""}
        
        query_vec = query_vec.reshape(1, -1).astype(np.float32)
        distances, indices = _vector_db.search(query_vec, top_k)
        
        results = []
        for idx in indices[0]:
            if idx < len(_texts):
                results.append({
                    "content": _texts[idx],
                    "source": _sources[idx],
                    "distance": float(distances[0][list(indices[0]).index(idx)])
                })
        
        print("Found", len(results), "results")
        
        # 检查点3 - 检索后
        if _generation_stop_flag:
            print("⏹️ 生成被取消（检索后）")
            return {"answer": "⏹️ 生成已取消", "sources": [], "context": ""}
        
        if not results:
            return {
                "answer": "⚠️ 知识库中未找到相关内容，请尝试换一个问题。",
                "sources": [],
                "context": ""
            }
        
        context_parts = []
        sources = []
        for i, doc in enumerate(results):
            content = doc["content"].strip()
            source = doc.get("source", "未知来源")
            context_parts.append(f"【参考资料 {i+1}】\n{content}")
            sources.append(source)
        
        context = "\n\n".join(context_parts)
        prompt = f"""你是一个计算机科学助教。你必须严格遵守以下规则：
1. 只回答计算机科学相关的问题（编程、算法、数据结构、操作系统、网络、数据库等）
2. 如果问题与计算机无关，礼貌地拒绝回答，并引导用户提出计算机相关的问题
3. 回答必须基于提供的参考资料
4. 回答不超过200字，简洁直接
5. 禁止使用emoji、禁止使用非中文语言
6. 如果参考资料与问题无关，直接说"知识库中暂无相关信息"
用户问题：{question}

参考资料（仅供参考，如果与问题无关可以忽略）：
{context}

回答要求：
- 直接回答问题，不要复述问题
- 不要刻意标注"引用来源"
- 如果参考资料与问题无关，可以用你自己的知识回答
- 回答要自然、简洁，不要超过400字
- 只回答计算机领域（编程、算法、OS、网络、数据库）
- 与计算机无关的问题，回复："请提出计算机相关的问题"

回答："""
        
        input_text = prompt
        
        # 检查点4 - 生成前
        if _generation_stop_flag:
            print("⏹️ 生成被取消（生成前）")
            return {"answer": "⏹️ 生成已取消", "sources": [], "context": ""}
        
        inputs = _tokenizer(input_text, return_tensors="pt", trust_remote_code=True)
        
        if torch.cuda.is_available():
            inputs = {k: v.to('cuda') for k, v in inputs.items()}
        
        # 检查点5 - 生成前（再次检查）
        if _generation_stop_flag:
            print("⏹️ 生成被取消（生成前，二次检查）")
            return {"answer": "⏹️ 生成已取消", "sources": [], "context": ""}
        
        print("开始生成回答...")
        
        # 一次性生成
        outputs = _model.generate(
            **inputs,
            max_new_tokens=200,
            temperature=0.2,
            do_sample=True,
            top_p=0.92,
            repetition_penalty=1.2,
            pad_token_id=_tokenizer.eos_token_id,
            top_k=50,
            early_stopping=True,
            stopping_criteria=StoppingCriteriaList([GenerationStopCriteria()])
        )
        
        # 检查点6 - 生成后
        if _generation_stop_flag:
            print("⏹️ 生成被取消（生成后）")
            return {"answer": "⏹️ 生成已取消", "sources": [], "context": ""}
        
        answer = _tokenizer.decode(outputs[0], skip_special_tokens=True)
        # 尝试提取"回答："之后的内容
        if "回答：" in answer:
            answer = answer.split("回答：")[-1].strip()
        else:
            answer = answer.replace(input_text, "").strip()

        # 如果回答为空或太短，给出友好提示
        if not answer or len(answer) < 5:
            answer = "抱歉，我没有理解您的问题，请换一种方式提问。"
        
        print("Generation completed")
        
        return {
            "answer": answer,
            "sources": sources,
            "context": context
        }
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print("rag_query error:", error_detail)
        return {
            "answer": f"⚠️ 生成回答时出错：{str(e)}",
            "sources": [],
            "context": ""
        }


if __name__ == "__main__":
    init_rag()
    print("\n" + "=" * 60)
    print("RAG 服务已启动（交互模式）")
    print("输入问题开始问答，输入 q 退出")
    print("=" * 60)
    while True:
        question = input("\nYou: ").strip()
        if question.lower() in ['q', 'quit', 'exit']:
            break
        if not question:
            continue
        result = rag_query(question)
        print("\nAssistant:", result['answer'])
        if result['sources']:
            print("\nSources:", ', '.join(result['sources']))
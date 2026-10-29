# rag_qa.py
import torch
import numpy as np
from transformers import AutoTokenizer, AutoModel, AutoModelForCausalLM, pipeline
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

# ============ 配置路径 ============
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = str(PROJECT_ROOT / "models" / "llm" / "qwen--Qwen2.5-7B-Instruct" / "snapshots" / "master")
VECTOR_DB_PATH = str(PROJECT_ROOT / "data" / "vector_db")

# ============ 1. 自定义 Embeddings（纯 transformers，不依赖 sentence_transformers） ============
class HuggingFaceEmbeddingsNoTrainer:
    def __init__(self, model_name="sentence-transformers/all-MiniLM-L6-v2"):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name)
        self.model.eval()
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = self.model.to(self.device)

    def embed_documents(self, texts):
        """批量生成向量"""
        embeddings = []
        for text in texts:
            emb = self.embed_query(text)
            embeddings.append(emb)
        return embeddings

    def embed_query(self, text):
        """生成单个向量的均值池化"""
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
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

    def embed_documents_wrapper(self, texts):
        return self.embed_documents(texts)

# ============ 2. 加载知识库 ============
print("正在加载知识库...")
embeddings = HuggingFaceEmbeddingsNoTrainer()

# ✅ 关键修复：加载时指定 embedding_function 为 embed_query 方法
vector_db = FAISS.load_local(
    VECTOR_DB_PATH,
    embeddings.embed_query,  # ✅ 传方法，不是类实例
    allow_dangerous_deserialization=True
)
print("✅ 知识库加载成功！")

# ============ 3. 加载 Qwen 模型（使用 pipeline） ============
print("正在加载 Qwen 模型...")

pipe = pipeline(
    "text-generation",
    model=MODEL_PATH,
    torch_dtype=torch.float16,
    device_map="cuda:0",
    trust_remote_code=True,
    max_new_tokens=512,
    temperature=0.3,
    do_sample=True,
    top_p=0.9,
    repetition_penalty=1.05
)
print("✅ 模型加载成功！")

# ============ 4. RAG 问答函数 ============
def rag_answer(question, top_k=3):
    print(f"\n🔍 正在检索知识库...")
    results = vector_db.similarity_search(question, k=top_k)
    
    context_parts = []
    sources = []
    for i, doc in enumerate(results):
        content = doc.page_content.strip()
        source = doc.metadata.get('source', '未知来源')
        context_parts.append(f"【参考资料 {i+1}】\n{content}")
        sources.append(source)
    
    context = "\n\n".join(context_parts)
    
    prompt = f"""你是一位计算机学科的专业教师。请基于以下参考资料回答用户的问题。

【参考资料】
{context}

【用户问题】
{question}

【要求】
1. 回答必须基于参考资料，不得编造内容
2. 如果参考资料中没有相关信息，请明确告知用户
3. 回答要清晰、准确、有条理
4. 在回答末尾标注引用来源

【回答】"""

    messages = [
        {"role": "system", "content": "你是一位计算机学科的专业教师，基于给定的参考资料回答问题。"},
        {"role": "user", "content": prompt}
    ]
    
    outputs = pipe(messages, max_new_tokens=512)
    answer = outputs[0]['generated_text'][-1]['content']
    
    return answer, sources

# ============ 5. 交互式问答 ============
print("\n" + "=" * 60)
print("RAG 知识库问答系统（计算机学科）")
print("输入问题开始问答，输入 q 退出")
print("=" * 60)

while True:
    question = input("\n💬 你：").strip()
    if question.lower() in ['q', 'quit', 'exit']:
        print("再见！")
        break
    if not question:
        continue
    
    try:
        answer, sources = rag_answer(question)
        print(f"\n🤖 助手：{answer}")
        print(f"\n📚 参考来源：")
        for i, src in enumerate(sources, 1):
            print(f"  [{i}] {src}")
    except Exception as e:
        print(f"\n❌ 出错：{e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "-" * 60)
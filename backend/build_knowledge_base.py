# build_knowledge_base_v2.py
import os
import glob
import pickle
import numpy as np
import torch
from transformers import AutoTokenizer, AutoModel
from sentence_transformers import SentenceTransformer

# ============ 配置路径 ============
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_PATH = os.path.join(PROJECT_ROOT, "data", "knowledge_base_docs")
VECTOR_DB_PATH = os.path.join(PROJECT_ROOT, "data", "vector_db")
EMBEDDING_MODEL_ROOT = os.path.join(PROJECT_ROOT, "models")


# ============ 1. 查找本地 Embedding 模型 ============
def find_model_path():
    candidate_roots = [
        os.path.join(PROJECT_ROOT, "models", "embedding_model"),
        os.path.join(PROJECT_ROOT, "models", "damo--nlp_corom_sentence-embedding_english-base"),
        os.path.join(PROJECT_ROOT, "models"),
    ]
    for root in candidate_roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if "config.json" in filenames and ("model.safetensors" in filenames or "pytorch_model.bin" in filenames):
                print(f"✅ 找到模型：{dirpath}")
                return dirpath
    for root in candidate_roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if "config.json" in filenames:
                print(f"⚠️ 找到 config.json 但缺少模型权重，尝试使用：{dirpath}")
                return dirpath
    raise FileNotFoundError(f"未找到模型，请确认 {EMBEDDING_MODEL_ROOT} 下有完整的模型文件")


# ============ 2. 文档加载 ============
def load_documents():
    print("=" * 60)
    print("第一步：加载文档...")
    print("=" * 60)
    
    if not os.path.exists(KNOWLEDGE_PATH):
        print(f"错误：文档目录不存在 - {KNOWLEDGE_PATH}")
        return []
    
    all_docs = []
    
    # 加载 TXT
    print("\n正在加载 TXT 文件...")
    txt_files = glob.glob(os.path.join(KNOWLEDGE_PATH, "**", "*.txt"), recursive=True)
    for txt_path in txt_files:
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                text = f.read()
            if text.strip():
                all_docs.append({
                    "content": text,
                    "source": txt_path
                })
                print(f"  加载: {os.path.basename(txt_path)} ({len(text)} 字符)")
        except Exception as e:
            print(f"  警告：加载 {os.path.basename(txt_path)} 失败 - {e}")
    
    # 加载 MD
    print("\n正在加载 Markdown 文件...")
    md_files = glob.glob(os.path.join(KNOWLEDGE_PATH, "**", "*.md"), recursive=True)
    for md_path in md_files:
        try:
            with open(md_path, 'r', encoding='utf-8') as f:
                text = f.read()
            if text.strip():
                all_docs.append({
                    "content": text,
                    "source": md_path
                })
                print(f"  加载: {os.path.basename(md_path)} ({len(text)} 字符)")
        except Exception as e:
            print(f"  警告：加载 {os.path.basename(md_path)} 失败 - {e}")
    
    return all_docs


# ============ 3. 文档切片 ============
def split_documents(docs, chunk_size=500, overlap=50):
    print("\n" + "=" * 60)
    print("第二步：文档切片...")
    print("=" * 60)
    
    chunks = []
    separators = ["\n\n", "\n", "。", "！", "？", "；", " ", ""]
    
    for doc in docs:
        content = doc["content"]
        source = doc["source"]
        
        # 按分隔符切片
        current_text = content
        while current_text:
            # 找最近的分隔符
            best_pos = len(current_text)
            for sep in separators:
                pos = current_text.find(sep, 0, chunk_size)
                if pos != -1 and pos < best_pos:
                    best_pos = pos + len(sep)
            
            if best_pos > len(current_text) or best_pos == 0:
                best_pos = min(chunk_size, len(current_text))
            
            chunk = current_text[:best_pos].strip()
            if chunk:
                chunks.append({
                    "content": chunk,
                    "source": source
                })
            
            current_text = current_text[best_pos - overlap:] if best_pos - overlap > 0 else current_text[best_pos:]
            if len(current_text) < 50:
                break
    
    print(f"🧩 文档被分割为 {len(chunks)} 个片段")
    if chunks:
        print(f"📖 示例片段（前200字符）：")
        print("-" * 40)
        print(chunks[0]["content"][:200] + "...")
        print("-" * 40)
    
    return chunks


# ============ 4. 生成向量 ============
class LocalEmbeddings:
    def __init__(self, model_path):
        print(f"加载 Embedding 模型：{model_path}")
        try:
            if os.path.exists(model_path):
                print("使用本地embedding模型：", model_path)
                self.model = SentenceTransformer(model_path, device='cpu')
            else:
                print("使用预训练embedding模型：all-MiniLM-L6-v2")
                self.model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
        except Exception as e:
            print("加载本地模型失败，使用默认模型：", str(e))
            self.model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
        print("模型加载成功，设备：cpu")

    def embed_query(self, text):
        return self.model.encode(text).astype(np.float32)


# ============ 5. 主流程 ============
def main():
    # 加载文档
    docs = load_documents()
    if not docs:
        print("错误：没有找到任何可读取的文档")
        return
    
    # 切片
    chunks = split_documents(docs)
    if not chunks:
        print("错误：没有生成任何片段")
        return
    
    # 加载 Embedding 模型
    print("\n" + "=" * 60)
    print("第三步：生成向量并保存知识库...")
    print("=" * 60)
    
    model_path = find_model_path()
    embeddings = LocalEmbeddings(model_path)
    
    # 生成向量
    print("正在生成向量索引...")
    vectors = []
    texts = []
    sources = []
    
    for i, chunk in enumerate(chunks):
        if (i + 1) % 10 == 0:
            print(f"  处理中：{i+1}/{len(chunks)}")
        vec = embeddings.embed_query(chunk["content"])
        vectors.append(vec)
        texts.append(chunk["content"])
        sources.append(chunk["source"])
    
    # 转换为 numpy 数组
    vectors = np.array(vectors, dtype=np.float32)
    
    # 保存
    print(f"正在保存到：{VECTOR_DB_PATH}")
    os.makedirs(VECTOR_DB_PATH, exist_ok=True)
    
    # 保存向量和元数据
    with open(os.path.join(VECTOR_DB_PATH, "vectors.npy"), "wb") as f:
        np.save(f, vectors)
    
    with open(os.path.join(VECTOR_DB_PATH, "texts.pkl"), "wb") as f:
        pickle.dump(texts, f)
    
    with open(os.path.join(VECTOR_DB_PATH, "sources.pkl"), "wb") as f:
        pickle.dump(sources, f)
    
    # 保存维度信息
    with open(os.path.join(VECTOR_DB_PATH, "dimension.txt"), "w") as f:
        f.write(str(vectors.shape[1]))
    
    print("\n" + "=" * 60)
    print("✅ 知识库构建完成！")
    print(f"📁 保存位置：{VECTOR_DB_PATH}")
    print(f"📄 文档数量：{len(docs)} 份")
    print(f"🧩 片段数量：{len(chunks)} 个")
    print(f"📐 向量维度：{vectors.shape[1]}")
    print("=" * 60)


if __name__ == "__main__":
    main()
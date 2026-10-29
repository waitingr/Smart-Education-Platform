// kbcore —— 灵眸智教的检索打分内核（C++17，零第三方依赖，C ABI 动态库）
//
// 为什么有这个文件：检索是整条链路里唯一能靠工程手段压下来的部分。一次问答 23~40 秒里
// 模型推理占 99.9%，而改动前的 Python 实现每次 search() 都把每个知识块重新分词 4~5 次
// （81 块就要 15~36 ms，4.86 万块要 9 秒以上）。这里把「逐块打分」这一层 O(N×Q) 的循环
// 搬到 C++，分词仍留在 Python —— C++ 只接收已经分好的 token id 数组，因此两边看到的分词
// 结果逐字一致，可以用 scripts/verify_kb_equiv.py 做 top-10 逐位比对。
//
// 分工：
//   C++  ：BM25 打分、标题命中、正文覆盖率、成对概念惩罚、学科实体奖惩、权威度因子
//   Python：分词、向量余弦（numpy/BLAS）、短语加分、排序、引用格式化
//
// 关键约束：**这里任何一处与 backend/kb_service.py 的 _score_python 不一致，
// 检索名次就会变，引用编号跟着变**。改动后必须跑：
//     python scripts/verify_kb_equiv.py            （KB_ENGINE=cpp 时校验本内核）
//     python scripts/bench_kb.py --engine cpp
// 编译： scripts/build_kbcore.ps1（或手动 g++ -std=c++17 -O3 -shared -static ...）
// 为什么用 C ABI 而不是 pybind11：本机 Python 由 MSVC 编译，MinGW 产出的 .pyd 加载不了；
// C ABI 动态库经 ctypes 调用与编译器无关，也不需要 Visual Studio。
//
// 编译产物不入库（见 .gitignore），**本文件必须入库**——丢了就得重写并对齐语义。

#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace {

struct Index {
    int32_t n_docs = 0;
    int32_t n_vocab = 0;

    const int32_t* doc_lens = nullptr;     // [n_docs]
    const int64_t* title_ptr = nullptr;    // [n_docs + 1] 标题 CSR
    const int32_t* title_ids = nullptr;    // [title_len]
    int64_t title_len = 0;

    const int64_t* post_ptr = nullptr;     // [n_vocab + 1] 正文倒排 CSR
    const int32_t* post_doc = nullptr;     // [post_len]
    const int32_t* post_tf = nullptr;      // [post_len]
    int64_t post_len = 0;

    const int32_t* contrast_bits = nullptr;  // [n_docs] 成对概念位掩码
    const int64_t* entity_masks = nullptr;   // [n_docs] 学科实体位掩码
    const int32_t* authority = nullptr;      // [n_docs] 1..5

    double k1 = 1.5;
    double b = 0.75;
    double avg_len = 0.0;

    std::vector<double> idf;     // [n_vocab] 建索引时算好，0 表示该词不在正文出现
    std::vector<double> len_b;   // [n_docs] k1*(1-b+b*dl/avg_len)，避免每次查询重算
};

inline double contrast_penalty(int32_t ask_bits, int32_t doc_bits, double weight) {
    // 与 kb_service._contrast_penalty 等价：问 A 方、块里只有 B 方 → 扣分
    double penalty = 0.0;
    for (int pair = 0; pair < 16; ++pair) {   // int32 最多 16 对；真实的 12 对之外没有位
        const int shift_a = 2 * pair;
        const int shift_b = 2 * pair + 1;
        const int ask_a = (ask_bits >> shift_a) & 1;
        const int ask_b = (ask_bits >> shift_b) & 1;
        if (ask_a == ask_b) continue;         // 没问，或两边都问了 → 不惩罚
        const int has_a = (doc_bits >> shift_a) & 1;
        const int has_b = (doc_bits >> shift_b) & 1;
        if (ask_a && has_b && !has_a) {
            penalty += weight;
        } else if (ask_b && has_a && !has_b) {
            penalty += weight;
        }
    }
    return penalty > 0.5 ? 0.5 : penalty;
}

inline double entity_score(int64_t asked_mask, int64_t doc_mask, double bonus, double penalty) {
    // 与 kb_service._entity_score 等价：点名的实体在本块出现 → 加分；块里只讲别的实体 → 小扣分
    if (asked_mask == 0 || doc_mask == 0) return 0.0;
    if ((asked_mask & doc_mask) != 0) return bonus;
    return -penalty;
}

}  // namespace

extern "C" {

const char* kbc_version(void) {
    return "kbcore 1.0 (BM25 + 学科消歧, C++17)";
}

// 建索引：只保存指针（数组由 Python 侧的 CppIndex 持有），并把 idf / 长度归一化预算好。
void* kbc_index_create(int32_t n_docs,
                       int32_t n_vocab,
                       const int32_t* doc_lens,
                       const int64_t* title_ptr,
                       const int32_t* title_ids,
                       int64_t title_len,
                       const int64_t* post_ptr,
                       const int32_t* post_doc,
                       const int32_t* post_tf,
                       int64_t post_len,
                       const int32_t* contrast_bits,
                       const int64_t* entity_masks,
                       const int32_t* authority,
                       float k1,
                       float b) {
    if (n_docs <= 0 || n_vocab <= 0) return nullptr;
    if (!doc_lens || !title_ptr || !post_ptr || !post_doc || !post_tf) return nullptr;
    if (!contrast_bits || !entity_masks || !authority) return nullptr;

    Index* index = new (std::nothrow) Index();
    if (!index) return nullptr;

    index->n_docs = n_docs;
    index->n_vocab = n_vocab;
    index->doc_lens = doc_lens;
    index->title_ptr = title_ptr;
    index->title_ids = title_ids;
    index->title_len = title_len;
    index->post_ptr = post_ptr;
    index->post_doc = post_doc;
    index->post_tf = post_tf;
    index->post_len = post_len;
    index->contrast_bits = contrast_bits;
    index->entity_masks = entity_masks;
    index->authority = authority;
    index->k1 = static_cast<double>(k1);
    index->b = static_cast<double>(b);

    double total_len = 0.0;
    for (int32_t doc = 0; doc < n_docs; ++doc) {
        total_len += static_cast<double>(doc_lens[doc] > 0 ? doc_lens[doc] : 1);
    }
    // 与 text_utils.BM25 一致：avg_len = sum(doc_lens)/N，doc_len=0 时按 1 处理
    index->avg_len = total_len / static_cast<double>(n_docs);

    index->idf.assign(static_cast<size_t>(n_vocab), 0.0);
    for (int32_t term = 0; term < n_vocab; ++term) {
        const int64_t df = post_ptr[term + 1] - post_ptr[term];
        if (df <= 0) continue;   // 只在标题里出现的词：Python 侧同样没有 idf，跳过
        const double n = static_cast<double>(df);
        index->idf[static_cast<size_t>(term)] =
            std::log(1.0 + (static_cast<double>(n_docs) - n + 0.5) / (n + 0.5));
    }

    index->len_b.assign(static_cast<size_t>(n_docs), 0.0);
    const double avg = index->avg_len > 0.0 ? index->avg_len : 1.0;
    for (int32_t doc = 0; doc < n_docs; ++doc) {
        const double dl = static_cast<double>(doc_lens[doc] > 0 ? doc_lens[doc] : 1);
        index->len_b[static_cast<size_t>(doc)] = index->k1 * (1.0 - index->b + index->b * dl / avg);
    }
    return index;
}

void kbc_index_free(void* handle) {
    delete static_cast<Index*>(handle);
}

// 逐块打分：写满 lexical（BM25 原始分）、delta（附加分）、auth（权威度因子）三个 float 数组。
int32_t kbc_search(void* handle,
                   const int32_t* query_ids,
                   int32_t n_query,
                   int32_t query_total,
                   int32_t ask_bits,
                   int64_t query_entity_mask,
                   const int8_t* prefer_flags,
                   float authority_weight,
                   float title_weight,
                   float coverage_weight,
                   float prefer_bonus,
                   float contrast_weight,
                   float entity_bonus,
                   float entity_penalty,
                   float* out_lexical,
                   float* out_delta,
                   float* out_auth) {
    Index* index = static_cast<Index*>(handle);
    if (!index || !out_lexical || !out_delta || !out_auth) return -1;
    if (n_query < 0) return -1;

    const int32_t n_docs = index->n_docs;
    const int32_t n_vocab = index->n_vocab;
    const double k1 = index->k1;

    std::vector<double> lexical(static_cast<size_t>(n_docs), 0.0);
    std::vector<int32_t> hits(static_cast<size_t>(n_docs), 0);
    std::vector<uint8_t> query_flag(static_cast<size_t>(n_vocab), 0);

    // 1) 标出查询里出现过的词（自动去重），与 Python 的 set(search_tokens) 一致。
    //    同时把去重后的词号收进 terms：后面只遍历这几个词，不扫整张词表（学科级词表可能几十万）。
    std::vector<int32_t> terms;
    terms.reserve(static_cast<size_t>(n_query > 0 ? n_query : 0));
    for (int32_t i = 0; i < n_query; ++i) {
        const int32_t term = query_ids[i];
        if (term < 0 || term >= n_vocab) continue;
        if (query_flag[static_cast<size_t>(term)]) continue;
        query_flag[static_cast<size_t>(term)] = 1;
        terms.push_back(term);
    }

    // 2) BM25：按词遍历倒排表累加；同时统计「命中了几个不同的查询词」用于覆盖率
    const double qn = query_total > 0 ? static_cast<double>(query_total) : 1.0;
    for (int32_t term : terms) {
        const double idf = index->idf[static_cast<size_t>(term)];
        if (idf <= 0.0) continue;   // 标题专属词，正文没有 → 不计 BM25（与 Python 相同）
        const int64_t begin = index->post_ptr[term];
        const int64_t end = index->post_ptr[term + 1];
        for (int64_t p = begin; p < end; ++p) {
            const int32_t doc = index->post_doc[p];
            const double tf = static_cast<double>(index->post_tf[p]);
            const double denom = tf + index->len_b[static_cast<size_t>(doc)];
            lexical[static_cast<size_t>(doc)] += idf * tf * (k1 + 1.0) / denom;
            hits[static_cast<size_t>(doc)] += 1;   // 每个词对同一块只出现一次
        }
    }

    // 3) 逐块汇总附加分、权威度因子
    const double tw = static_cast<double>(title_weight);
    const double cw = static_cast<double>(coverage_weight);
    const double pb = static_cast<double>(prefer_bonus);
    const double kw = static_cast<double>(contrast_weight);
    const double eb = static_cast<double>(entity_bonus);
    const double ep = static_cast<double>(entity_penalty);
    const double aw = static_cast<double>(authority_weight);

    for (int32_t doc = 0; doc < n_docs; ++doc) {
        // 标题命中数：标题词表可能含正文没有的词，所以单独走标题 CSR
        int32_t title_hits = 0;
        const int64_t t_begin = index->title_ptr[doc];
        const int64_t t_end = index->title_ptr[doc + 1];
        for (int64_t p = t_begin; p < t_end; ++p) {
            const int32_t term = index->title_ids[p];
            if (term >= 0 && term < n_vocab && query_flag[static_cast<size_t>(term)]) {
                ++title_hits;
            }
        }

        double extra = 0.0;
        if (query_total > 0) {
            extra += tw * (static_cast<double>(title_hits) / qn);
            extra += cw * (static_cast<double>(hits[static_cast<size_t>(doc)]) / qn);
        }
        if (prefer_flags && prefer_flags[doc]) extra += pb;
        extra -= contrast_penalty(ask_bits, index->contrast_bits[doc], kw);
        extra += entity_score(query_entity_mask, index->entity_masks[doc], eb, ep);

        out_lexical[doc] = static_cast<float>(lexical[static_cast<size_t>(doc)]);
        out_delta[doc] = static_cast<float>(extra);
        const int32_t authority = index->authority[doc];
        out_auth[doc] = static_cast<float>(1.0 - aw * static_cast<double>(authority - 1));
    }
    return 0;
}

}  // extern "C"
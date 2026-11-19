# Prior art

Ideas Kairos builds on, what was taken, and what was not. No code was copied from any of these projects; each adopted item is an independent implementation of a concept.

| Project | Idea | In Kairos |
|---|---|---|
| [Zep / Graphiti](https://github.com/getzep/graphiti) | Bi-temporal facts: when a fact held versus when it was recorded; superseded, not deleted | `valid_from` / `valid_until` / `superseded_by_id` on knowledge items; only valid facts are served; the cache keys on item versions |
| [Zep / Graphiti](https://github.com/getzep/graphiti) | Layered memory (episodes, entities, communities) | Confirmed the layered approach; Kairos's layers follow org structure instead |
| [Zep / Graphiti](https://github.com/getzep/graphiti) | Hybrid retrieval (cosine + BM25 + graph, fused with reciprocal rank fusion) | Not built. Retrieval is embedding similarity only |
| [Aider repo map](https://aider.chat/docs/repomap.html) | PageRank over a reference graph, then fill a token budget by rank | PageRank over same-scope similarity edges as a ranking factor. On the benchmark it slightly hurt recall (BENCH-002) |
| [Letta / MemGPT](https://github.com/letta-ai/letta) | The agent pages in more memory on demand with a tool call | `search` MCP tool and `POST /v1/search_knowledge` |
| [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph) | Watch files and re-index on change, with debounce | `kairos watch` re-extracts git commits on new commits. Its "index is stale" banner is not built |
| [Mem0](https://github.com/mem0ai/mem0) | Classify new memories as add / update / delete / no-op; extract facts from a conversation in one LLM call | Not built. Proposals are always new rows, reviewed by a human |
| [Microsoft LLMLingua](https://github.com/microsoft/LLMLingua) | Prompt compression with a small language model | Not built. Compression is truncation or an optional LLM summary of one item |
| [Microsoft GraphRAG](https://microsoft.github.io/graphrag/) | Community detection with LLM summaries as a higher tier | Not built: not needed at the scale Kairos targets |
| [GPTCache](https://github.com/zilliztech/GPTCache), [RedisVL](https://github.com/redis/redis-vl-python) | Semantic caching of LLM responses by embedding similarity | Informed the bundle cache. Early, unpublished experiments with MiniLM embeddings suggested a sharp recall cliff between thresholds 0.80 and 0.85; the threshold Kairos ships with (0.90) is instead set by its own measurement (BENCH-003). Neither library partitions by permission, which is the main difference |
| [vLLM](https://docs.vllm.ai/en/stable/design/prefix_caching/) / [SGLang](https://github.com/sgl-project/sglang) prefix caching | Reuse KV cache inside the inference engine | A different layer. Kairos bundles are deterministic for the same inputs, so a downstream prefix cache can still hit |

-- pgvector/pgvector 镜像已把扩展的二进制编译进去了，
-- 这里只需要在目标数据库里启用它（CREATE EXTENSION 是幂等的，重复跑不报错）。
CREATE EXTENSION IF NOT EXISTS vector;

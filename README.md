# 1. Install a clean Python 3.10 virtual environment using uv
uv venv --python 3.10
source .venv/bin/activate

# 2. Install project dependencies (editable mode)
uv pip install -e .

# 3. Install Ollama (WSL Linux environment)
curl -fsSL https://ollama.ai/install.sh | sh

# 5. Pull a small model to test (example: llama3.2:1b)
ollama pull llama3.2:1b

# 6. Test a basic prompt with ollama 
## a) cli
ollama run llama3.2:1b "Hello!"

## b) python
uv run python src/ollama_run.py

## c) postman
Header: http://localhost:11434/api/chat
Bodey : 
{
  "model": "llama3.2:1b",
  "messages": [
    {
      "role": "user",
      "content": "Hello from Postman!"
    }
  ],
  "stream": false
}


## Why I Chose Ollama Instead of vLLM: https://docs.ollama.com/

vLLM is optimized for GPU systems and requires heavy C++ builds, large RAM, and CUDA-related dependencies that aren’t practical on my CPU-only laptop running WSL2. Ollama is lightweight, fully open-source, runs efficiently on CPUs with no compilation overhead, and integrates cleanly with Docker and Google’s Agent Development Kit via an OpenAI-compatible wrapper—making it the right fit for my setup.
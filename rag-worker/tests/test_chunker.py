from rag.chunker import chunk_pages, Chunk


class TestChunker:
    def test_single_page_single_chunk(self):
        pages = ["word " * 100]
        chunks = chunk_pages(pages, chunk_size=512, chunk_overlap=64)
        assert len(chunks) == 1
        assert chunks[0].page == 0
        assert chunks[0].chunk_index == 0

    def test_multiple_chunks_per_page(self):
        text = "word " * 600
        pages = [text]
        chunks = chunk_pages(pages, chunk_size=100, chunk_overlap=10)
        assert len(chunks) > 1
        assert chunks[0].page == 0
        assert chunks[1].page == 0
        assert chunks[1].chunk_index == 1

    def test_multiple_pages(self):
        pages = ["page zero text", "page one text", "page two text"]
        chunks = chunk_pages(pages, chunk_size=512, chunk_overlap=64)
        assert len(chunks) == 3
        assert chunks[0].page == 0
        assert chunks[1].page == 1
        assert chunks[2].page == 2

    def test_empty_page_skipped(self):
        pages = ["", "content here"]
        chunks = chunk_pages(pages, chunk_size=512, chunk_overlap=64)
        assert len(chunks) == 1
        assert chunks[0].page == 1

    def test_overlap_produces_shared_words(self):
        text = " ".join(f"w{i}" for i in range(200))
        pages = [text]
        chunks = chunk_pages(pages, chunk_size=50, chunk_overlap=10)
        if len(chunks) > 1:
            first_words = chunks[0].text.split()
            second_words = chunks[1].text.split()
            overlap = set(first_words) & set(second_words)
            assert len(overlap) > 0

    def test_empty_input(self):
        chunks = chunk_pages([], chunk_size=512, chunk_overlap=64)
        assert chunks == []

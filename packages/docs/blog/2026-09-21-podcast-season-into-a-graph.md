---
slug: podcast-season-into-a-graph
title: "A Podcast Season Into a Knowledge Graph, Offline, With Timestamps"
authors: [denis]
tags: [selfhosted, graphrag, ai, opensource]
date: 2026-09-21
draft: true
description: Drop a folder of episodes into Chaos Cypher and Whisper transcribes them on your CPU, the graph shows who said what about which topic across the season, and every citation points at the seconds of the recording it came from.
---

Most "chat with your podcast" tools give you a transcript and a search box. Search returns a paragraph, the paragraph says something interesting, and then you go hunting through a two-hour episode for the moment it was said.

Chaos Cypher has transcribed audio and video locally for a while: Whisper on your own CPU, nothing uploaded anywhere. What it did not do until this release is remember *when*. Chunks of a transcript now carry the seconds of the recording they came from, the way chunks of a PDF carry a page number. A citation into an episode reads "at 1:27–2:13", and the graph on top of the transcripts is the part no transcript tool gives you at all.

<!-- truncate -->

## What You'll Build

- A folder of episodes transcribed on your own machine, no API key, no upload
- One knowledge graph across all of them: people, topics, products, decisions, and how they connect
- Chunks and citations that carry a time range into the recording
- A question answered across episodes, with the moment to jump to

You will need `ffmpeg` installed, and Chaos Cypher's media dependencies. In the Docker image that is the `INCLUDE_MEDIA=1` build; with the CLI it is a `pip install faster-whisper`. Nothing else.

## Step 1: Get some audio

If you have episodes, skip this step. If you want to reproduce the post exactly without anyone's licensed audio, synthesize one. This is the two-minute, two-voice episode we used, generated with `espeak-ng` and stitched with `ffmpeg`:

```bash
espeak-ng -v en-us+m3 -w 01.wav "Welcome to Signal and Noise, episode twelve. I'm Maya, and with me is Theo."
espeak-ng -v en-us+f3 -w 02.wav "Today we are talking about the message queue. Last spring we migrated from Redis to Valkey."
# ... one line per turn ...
printf "file '01.wav'\nfile '02.wav'\n" > parts.txt
ffmpeg -f concat -safe 0 -i parts.txt -ar 16000 -ac 1 episode-12.wav
```

Synthetic speech is a harsh test for a speech model. Whisper's `base` model misheard "Cortex" as "Corgax" and "queues" as "Q's" in our run, which is a fair warning that the default model trades accuracy for speed. `whisper_model_size: small` in the loader settings fixes most of it at the cost of a slower first transcription.

## Step 2: Add the episodes

```bash
chaoscypher source add ./episodes/ --domain technical
```

One line per file goes by in the pipeline view: upload, index, extract, commit. The index step is where Whisper runs. For our two-minute episode on a laptop CPU that was 21 seconds; a real hour-long episode is a few minutes. Extraction then reads the transcript in chunks and pulls out the entities and relationships with the same evidence check every other loader gets, so an entity has to point at the transcript sentence that supports it.

The `--domain technical` flag is optional. Chaos Cypher auto-detects a domain from the text and parks the source for confirmation; forcing one skips the gate for a batch.

<!-- optional screenshot: Sources list showing a folder of episodes with the pipeline stages complete -->

## Step 3: Look at a chunk

Open an episode's source page and the Chunks tab. Next to each chunk index there is now a time range:

```
#1  0:00–0:58   Welcome to Signal and Noise, episode 12. I'm Maya, and with me...
#2  0:46–1:39   Walk me through it. Two things. Cortex, the API server, enqueues...
#3  1:27–2:13   One more thing before we wrap up. Cancellation. How does a running...
```

Those are real numbers from our episode. The ranges overlap because chunks overlap: a chunk that starts mid-sentence carries the start time of the transcript segment it begins in and the end time of the segment it ends in, so the range is the whole span you would need to listen to. Whisper's segment boundaries are what make this possible; the loader used to throw them away when it joined the transcript, and now it keeps them.

![Source overview with the pipeline stages and entity distribution for one document](/img/screenshots/app-source-overview.png)

<!-- optional screenshot: Chunks tab of an episode showing the m:ss–m:ss time badges beside chunk numbers -->

## Step 4: Ask across the season

Now the graph. Every episode is a source; every person, product and decision mentioned is an entity with citations back to the chunks it was extracted from; the relationships between them are edges with a justification. Ask in chat, or through any MCP client with the [server mounted](/docs/user-guide/mcp):

> How does a running task get cancelled, and which episode covered it?

The answer cites the chunk at 1:27–2:13 of episode 12, because that is where Theo explains the cooperative cancel flag. In a real season the same question pulls chunks from every episode that touched the subject, and the graph tells you that the person explaining it in episode 12 is the same person who set up the queue in episode 3.

Timestamps travel everywhere a chunk goes: the Chunks tab, the citation chips in chat, the sources panel of an entity page, the search API, and the chunk results the MCP server returns to Claude or Cursor.

<!-- optional screenshot: chat answer with a citation chip reading "At 1:27–2:13" -->

**In plain English:** the graph tells you who said what about which topic across every episode, and each answer tells you the minute to scrub to.

## Why local matters here

Audio is the medium people are most reluctant to upload. Interviews, meetings, lectures, a founder's voice notes: the content is personal and the speaker never agreed to a third party's terms. Whisper on your CPU means the recording never leaves the disk it is on, and the graph built from it lives in a SQLite file you can back up or delete.

## What it does not do yet

There is no play button. The time badge tells you where to scrub; it does not embed an audio player and seek to it, because the original media is kept in the source's staging directory rather than served through the API. Speaker labels are not extracted either: Whisper does not diarize, so "who said it" comes from the graph's entities, not from a voice model. And the timestamps do not yet travel inside a `.ccx` package; a mounted podcast package answers with citations but without the minute mark, which is on the list.

## Next steps

- Loader settings for Whisper model size, device and timeout are in the [loaders guide](/docs/user-guide/loaders).
- Once a season is in, [export it as a package](/docs/user-guide/import-export) and share the graph without sharing the audio.
- The [ten-minute quickstart](/blog/graphrag-ollama-10-minutes) covers the Ollama setup that extraction needs.

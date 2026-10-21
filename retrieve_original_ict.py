import json, pathlib, traceback
from youtube_transcript_api import YouTubeTranscriptApi
VIDEOS = {
 "ICT_2022_EP3":"nQfHZ2DEJ8c",
 "BREAKER":"UrS-mtGHtAA",
 "OTE_EQUILIBRIUM_DISCOUNT":"qC0LogyIk2I",
 "SILVER_BULLET":"tRq1hyGGtl4",
 "MITIGATION_BLOCK":"FOUzW0QmsfI",
}
out=pathlib.Path("original_ict_transcripts");out.mkdir(exist_ok=True)
results={}
for model,vid in VIDEOS.items():
 try:
  api=YouTubeTranscriptApi()
  transcript=api.fetch(vid,languages=["en"])
  lines=[{"start_seconds":round(x.start,2),"duration_seconds":round(x.duration,2),"text":x.text} for x in transcript]
  (out/(model+".json")).write_text(json.dumps({"video_id":vid,"lines":lines},ensure_ascii=False,indent=2),encoding="utf-8")
  (out/(model+".txt")).write_text("\n".join(f"[{int(x['start_seconds']//60):02d}:{int(x['start_seconds']%60):02d}] {x['text']}" for x in lines),encoding="utf-8")
  results[model]={"status":"retrieved","lines":len(lines),"video_id":vid}
 except Exception as e:
  results[model]={"status":"failed","video_id":vid,"error":str(e)}
(out/"manifest.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
print(json.dumps(results,indent=2))

import pytubefix
from pytubefix import Stream

yt = pytubefix.YouTube('https://www.youtube.com/watch?v=OC7hwUCzPiw')

for stream in yt.streams:
    stream: Stream
    print(f'res: {stream.resolution} | abr: {stream.abr}')
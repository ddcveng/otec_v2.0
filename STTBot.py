import discord
import asyncio
import speech_recognition as sr
from threading import Thread
import youtube_dl
import auth

# bot token and wit.ai api key
TOKEN = auth.TOKEN
WIT_AI_KEY = auth.WIT_AI_KEY

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# we need a sink for the listen function, so we just define our own
# extremely simple: just appends data to a byte array buffer
class BufSink(discord.reader.AudioSink):
	def __init__(self):
		# byte array to store stuff
		self.bytearr_buf = bytearray()
		# sample width, which is (bit_rate/8) * channels
		self.sample_width = 2
		# 48000Hz sampling rate
		# doubled, because speech_recognition needs mono and we've got stereo
		self.sample_rate = 96000
		# calculated bytes per second, sample_rate * sample_width
		# we need this to know what slices we can take from the buffer
		# would be 96000, but mono
		self.bytes_ps = 192000

	# just append data to the byte array
	def write(self, data):
		self.bytearr_buf += data.data

	# to prevent the buffer from getting immense, we just cut the part we've
	# just read from it, using the index calculated when we extracted the part
	def freshen(self, idx):
		self.bytearr_buf = self.bytearr_buf[idx:]

# global var - needed to stop the thread
close_flag = False

# client bot class
class Deffy(discord.Client):
	# init variables
	def __init__(self):
		super().__init__()
		# save the channel we need to post to
		self.target_channel = None
		# the thread object
		self.post_thread = None
		# buffer to hold info
		self.buffer = BufSink()

	# post some sanity messages on start-up
	async def on_ready(self):
		print()
		print("Logged in as")
		print(self.user.name)
		print(self.user.id)
		print("----------")
		print("Discord.py version")
		print(discord.__version__)
		print("----------")
		print()

	# wait for a message to interact with the user
	async def on_message(self, message):
		# notify the thread we're closing
		global close_flag
		
		# don't respond to ourselves
		if message.author == self.user:
			return False

		# handle closing
		if message.content.lower().startswith("$close"):
			# send a message to ack the command
			await message.channel.send("Got it, shutting down...")

			# the polite thing to do is close any active voice connections properly
			if self.voice_clients:
				for vc in self.voice_clients:
					await vc.disconnect()
			# set the flag and wait for the thread to end
			close_flag = True
			self.post_thread.join()

			# shut down the bot, then quit the program
			await self.close()
			quit()

		# handle disconnecting
		if message.content.lower().startswith("$leave"):
			# close any active voice connections. in theory, there's only one, but
			# could be extended for more
			if self.voice_clients:
				for vc in self.voice_clients:
					await vc.disconnect()
				# set the flag and wait for the thread to end
				close_flag = True
				self.post_thread.join()
			else:
				await message.channel.send("Sorry, you're not in a voice channel.")

		# handle summoning
		if message.content.lower().startswith("pot sem"):
			# if the user is not connect to a voice channel, but tries to summon,
			# just send a message and exit
			if message.author.voice is None:
				await message.channel.send("Sorry, you're not in a voice channel.")
			else:
				# check if we already have an active voice connection, and use that
				# one instead of creating another one
				if self.voice_clients:
					# store the channel we need to post our output to
					self.target_channel = message.channel
					# ack the command and inform the user
					await message.channel.send("jasné, idem do kanálu " +
						message.author.voice.channel.name + " a budem spievat do kanálu " +
						self.target_channel.name + ".")
					# use the existing voice connection to move to the new voice channel
					await self.voice_clients[0].move_to(message.author.voice.channel)
					# start a thread that will handle voice analysis
					# if it doesn't exist already
					if self.post_thread is None:
						self.post_thread = Thread(target=poster,
						                          args=(self, self.buffer, self.target_channel))
						self.post_thread.start()
					# start listening - user filter just listens to a certain user
					self.voice_clients[0].listen(discord.reader.UserFilter(
						self.buffer, message.author))
				else:
					# if we don't have an already active connection, create a new one
					self.target_channel = message.channel
					await message.channel.send("jasné, idem do kanálu " +
						message.author.voice.channel.name + " a budem spievat do kanálu " +
						self.target_channel.name + ".")
					# create a new voice client
					vc = await message.author.voice.channel.connect()
					# start a thread that will handle voice analysis,
					# if it doesn't exist already
					if self.post_thread is None:
						self.post_thread = Thread(target=poster,
						                          args=(self, vc, self.buffer, self.target_channel))
						self.post_thread.start()
					# start listening - user filter just listens to a certain user
					self.voice_clients[0].listen(discord.reader.UserFilter(
						self.buffer, message.author))

# thread that handles message posting and voice analysis
def poster(bot, voice_client, buffer, target_channel):
	global close_flag
	# instantiate our recognizer object
	recog = sr.Recognizer()
	# we don't want the thread to end, so just loop forever
	while True:
		# useless to try anything if we don't have anything in the buffer
		# wait until we have enough data for a 5-second voice clip in the buffer
		if len(buffer.bytearr_buf) > 960000:
			# get 5 seconds worth of data from the buffer
			idx = buffer.bytes_ps * 5
			slice = buffer.bytearr_buf[:idx]

			# if the slice isn't all 0s, create an AudioData instance with it,
			# needed by the speech_recognition lib
			if any(slice):
				# trim leading zeroes, should be more accurate
				idx_strip = slice.index(next(filter(lambda x: x!=0, slice)))
				if idx_strip:
					buffer.freshen(idx_strip)
					slice = buffer.bytearr_buf[:idx]
				# create the AudioData object
				audio = sr.AudioData(bytes(slice), buffer.sample_rate,
					buffer.sample_width)

				# send the data to get recognized
				try:
					msg = recog.recognize_wit(audio, key=WIT_AI_KEY)
				except sr.UnknownValueError:
					print("ERROR: Couldn't understand.")
				except sr.RequestError as e:
					print(f"ERROR: Could not request results from Wit.ai service; {e}")

				# if we send a msg with all 0s or something unintelligible,
				# we'll get a message, but it'll be empty
				if msg:
					# send the message to the async routine
					#asyncio.run_coroutine_threadsafe(target_channel.send(msg), bot.loop)
					asyncio.run_coroutine_threadsafe(play_song(bot, target_channel, voice_client, msg), bot.loop)
			# cut the part we just read from the buffer
			buffer.freshen(idx)

		# since it's an infinite loop, we need some way to break out, once the
		# program shuts down
		if close_flag:
			break

ffmpeg_options = {
    'options': '-vn'
	}

class YTDLSource(discord.PCMVolumeTransformer):
	def __init__(self, source, *, data, volume=0.5):
		super().__init__(source, volume)

		self.data = data

		self.title = data.get('title')
		self.url = data.get('url')

	@classmethod
	async def from_url(cls, url, *, loop=None, stream=False):
		loop = loop or asyncio.get_event_loop()
		data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
		if 'entries' in data:
			data = data['entries'][0]
		filename = data['url'] if stream else ytdl.prepare_filename(data)
		return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

async def play_song(bot, text_channel, voice_client, query):
	if not discord.opus.is_loaded():
		assert discord.opus.load_opus('libopus')
	query = query.split('play')
	if len(query) > 1:
		query = query[-1]
	
	await text_channel.send(f'Hľadám {query}...')
	try:
		info = ytdl.extract_info('ytsearch1:'+str(query), download=False, ie_key='YoutubeSearch')
	except TypeError:
		info = ytdl.extract_info('ytsearch1:'+query[0], download=False, ie_key='YoutubeSearch')
	url = "https://www.youtube.com/watch?v=" + info['entries'][0]['id']
	
	player = await YTDLSource.from_url(url, loop=bot.loop)
	await text_channel.send(player.title)
	voice_client.play(player, after=lambda e: print('Player error: %s' % e) if e else None)
	


client = Deffy()
client.run(TOKEN)
import os
import ntpath
import json
import logging


from chat_downloader import ChatDownloader
from .dataformat import * # YoutubeChatAnalytics, TwitchChatAnalytics
from .util import dprint
from urllib.parse import urlparse
from chat_downloader.sites.common import Chat

from .metadata import (
    __version__
    )

# Times in seconds dictating how granular the interval can be (how long the individual samples are)
MAX_INTERVAL = 120
MIN_INTERVAL = 1

# Can be set via CLI --debug flag
DEBUG = False

# Define the arguments for getting the chatlog using chat-downloader
chat_download_settings =  {
    "url" : None, # Set in get_chatlog_downloader()
    "message_types" : 'all',
    "output" : None, # If save-chatfile, set to that path before get_chatlog_downloader()
}

def get_chatlog_downloader(url: str):
    """
    Gets a chat-downloader generator using Xenonva's chat-downloader
    
    :param url: The URL of the past stream/VOD to download the chat from
    :param type: str
    :returns: The chatlog we have downloaded
    :rtype: chat_downloader.sites.common.Chat
    """
    # Provide url argument to the chat downloader
    chat_download_settings['url'] = url
 

    print("Getting chatlog using Xenonva's chat-downloader (https://github.com/xenova/chat-downloader)...")
    if(DEBUG):
        dprint(f"Chat download settings: {chat_download_settings}")

    try:
        chat = ChatDownloader().get_chat(
            chat_download_settings['url'], 
            message_types=[chat_download_settings['message_types']],
            output=chat_download_settings['output'])       # create a generator
    except Exception as exception:
        logging.critical("ERORR: Could not get chat: "+ str(exception))
        exit(1)

    print("Successfully retrieved chat generator:")
    print("\tTitle: %s" % (str(chat.title)))
    print("\tDuration: %s (%s seconds)" % (seconds_to_time(chat.duration), str(chat.duration)))
    print("""\033[1;31mNOTICE: Downloading chats from a url is the largest rate-limiting factor.
             \033[0;31mIf you intend to sample the data differently multiple times, consider using \033[1;33mchatfile\033[0;31m mode, or saving the chat data with \033[1;33m--save-chatfile\033[0;31m.\n\033[0m""")

    return chat

def check_chatlog_downloader_supported(chatlog: Chat, url: str):
    """
    Ensures that we are able to properly analyze the downloaded chatlog by
    enforcing the metadata matches expected values. 
    (Our own logic depends on certain things being true)

    If there is a breach of compliance, we exit (we consider this to be a fatal error)
    
    :param chatlog: The chatlog we have downloaded
    :type chatlog: chat_downloader.sites.common.Chat
    :param url: The URL of the video we have downloaded the log from
    :type url: str

    Currently we ensure:
        - The video/stream happened in the past (not currently live or scheduled)
        - The chat is downloaded from a supported platform that we have a proper
        way of parsing.
    """

    if(chatlog.status !='past'):
        logging.critical("ERROR: chat-analyzer does not support live or upcoming streams. Wait until the stream is over to use this tool!")
        exit(1)
    if(chatlog.status == None):
        logging.critical(f"ERROR: unrecognized stream status: {chatlog.status}. chat-analyzer must be able to confirm that the stream is over")
        exit(1)
    
    # NOTE: I don't currently know of a way to get the platform from the chatlog itself, so a quick operation here is a simple-enough solution
    platform = urlparse(url).netloc
    if(platform not in SUPPORTED_PLATFORMS):
        # NOTE: This is for platforms that the chat-downloader can handle but chat-analyzer can't
        logging.critical(f"ERROR: chat-analyzer does not currently support chatlogs from {platform}")
        exit(1)

def get_chatmsgs_from_chatfile(filepath: str):
    """Given a path to a chatfile (directly from Xenovas downloader or produced by 
    --save-chatfile-output) flag, extract the chat messages from the file into an array
    of chat messages.


    :param filepath: a path to a chatfile (directly from Xenovas downloader or produced by 
    --save-chatfile-output) flag
    :type filepath: str
    :returns: an array of chat messages
    :rtype: list[chat_downloader.sites.common.ChatMessage]
    """
    with open(filepath, 'r') as f:
        chat_messages = json.load(f)
        # dprint(len(chat_messages))
        # dprint([m['message'] for m in chat_messages])
    return chat_messages

def output_json_to_file(json_obj, filepath):
    if not os.path.exists(filepath): # code block adopted from Xenova's countinous_write.py
        directory = os.path.dirname(filepath)
        if directory:  # (non-empty directory - i.e. not in current folder)
            # must make parent directory
            os.makedirs(directory, exist_ok=True)
        # open(output_filepath, 'w').close()  # create an empty file

    with open(filepath, 'w') as f:
        json.dump(json.loads(json_obj), f, ensure_ascii=False, indent=4)
    
    print(f"Successfully wrote chat analytics to {filepath}")

def run(**kwargs):
    """Runs the chat-analyzer

    :returns: The chat analytics data as a dataclass
    :rtype: dataformat.ChatAnalytics (dataformat.YoutubeChatAnalytics or dataformat.TwitchChatAnalytics)
    """

    # Interpret and extract CLI arguments from kwargs
    DEBUG = kwargs.get('debug')
    if(DEBUG):
        for arg in kwargs:
            value = kwargs[arg]
            dprint(f"analyzing arg {arg}: {value}")
    
    source = kwargs.get('source') # Is either a url or a filepath
    # Optional
    platform = kwargs.get('platform') # Required if mode==chatfile, otherwise == None
    if(platform):
        # Immediately re-parse from shorthand into official NETLOC
        platform = SUPPORTED_PLATFORMS_SHORTHANDS[platform]
    # Mode arguments
    program_mode = kwargs.get('mode') # choices=["url", "chatfile", "reanalyze"]
    save_chatfile_output = kwargs.get('save_chatfile_output')
    # Processing (Sampling) arguments
    interval = kwargs.get('interval')
    print_interval = kwargs.get('print_interval')
    # Post-processing (Analyzing) arguments
    highlight_percentile = kwargs.get('highlight_percentile')
    highlight_metric = kwargs.get('highlight_metric')
    spike_sensitivity = kwargs.get('spike_sensitivity')
    # Output
    description = kwargs.get('description')
    output_filepath = kwargs.get('output')
    # Debugging
    msg_break = kwargs.get('break')


    # Consumed by the processing/post-processing logic in dataformat.py
    process_settings = ProcessSettings(print_interval=print_interval, msg_break=msg_break, highlight_percentile=highlight_percentile, highlight_metric=highlight_metric, spike_sensitivity=spike_sensitivity)


    # Check interval argument, we check the url arg's platform in check_chatlog_supported()
    # NOTE: We double check here in addition to in CLI
    if(interval > MAX_INTERVAL or interval < MIN_INTERVAL): 
        raise ValueError(f"Sample interval must be {MIN_INTERVAL} <= interval <= {MAX_INTERVAL}")

    # Get the chat using the chat downloader and ensure that we can work with that data
    chatlog: Chat
    if(program_mode=='url'):
        if(save_chatfile_output!=None):
            chat_download_settings['output']= save_chatfile_output
            print(f"Raw chat data file will be saved to {save_chatfile_output}")
        url = source
        platform = urlparse(url).netloc
        chatlog = get_chatlog_downloader(url)
        check_chatlog_downloader_supported(chatlog, url)
    elif(program_mode=='chatfile'):
        # Have to create the Chat object manually. 
        # We just have the chat messages so we have to guess on some fields (Limitation from Xenova's chat-downloader)
        # NOTE: We could require custom chatfiles with manually imposed fields, but that destroys compatibility w/ native Xenova chat-downloader
        chat_messages = get_chatmsgs_from_chatfile(source) 
        chat_msg_iterator = iter(chat_messages)       
        chat_title = ntpath.basename(source) # We don't have the title of original vid so file name is next best thing
        chat_duration = chat_messages[-1]['time_in_seconds'] # We don't have duration so we approximate by taking the last message's timestamp
        chat_status = 'past' # (has to be, since its a chatfile)

        chatlog = Chat(chat=chat_msg_iterator, title=chat_title, duration=chat_duration, status=chat_status)
        # NOTE: platform required to provided through CLI, so don't need to set it here
        # NOTE: We assume that its a supported platform because user had to provide a platform via CLI which checks it there
    elif(program_mode=='reanalyze'):
        # get platform in here for reanalyze mode
        raise NotImplementedError(f"Mode {program_mode} is not yet supported... oops :(")
    else:
        raise NotImplementedError(f"Unrecognized program mode: {program_mode}")


    # Next section: Create the proper type of ChatAnalytics object based on the platform
    chatAnalytics: ChatAnalytics
    duration = chatlog.duration
    

    if(platform == YOUTUBE_NETLOC):
        chatAnalytics = YoutubeChatAnalytics(duration=duration, interval=interval, description=description, program_version=__version__)
    elif(platform == TWITCH_NETLOC):
        chatAnalytics = TwitchChatAnalytics(duration=duration, interval=interval, description=description, program_version=__version__)
    else:
        logging.critical(
            "ERROR: No corresponding ChatAnalytics object.\n\
            NOTE: Should have caught this in supported platforms checks earlier...")
        exit(1)

    # Now, we can process & analyze the data!
    chatAnalytics.process_chatlog(chatlog, source, process_settings)
        
    # chatAnalytics now contains all analytical data. We can print/return as ncessary
   
    json_obj = chatAnalytics.to_JSON()

    if(output_filepath==None): # If user did not specify an output filepath, use this default convention
        output_filepath =chatlog.title+'.json'
    output_json_to_file(json_obj, output_filepath)     
    
   

    return chatAnalytics


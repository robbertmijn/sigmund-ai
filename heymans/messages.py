import logging
import json
import zlib
import time
from pathlib import Path
from .model import model
from . import prompt
from . import config
from langchain.schema import HumanMessage, AIMessage, SystemMessage
logger = logging.getLogger('heymans')


class Messages:
    
    def __init__(self, heymans, persistent=False):
        self._heymans = heymans
        self._condensed_text = None
        metadata = self._metadata()
        metadata['search_model'] = 'Welcome message'
        self._message_history = [('assistant', self.welcome_message(), 
                                  metadata)]
        self._condensed_message_history = [
            (role, content) for role, content, metadata
            in self._message_history[:]]
        self._session_path = Path(f'sessions/{heymans.user_id}.json')
        self._persistent = persistent
        if self._persistent:
            self._load()
        
    def __len__(self):
        return len(self._message_history)
        
    def __iter__(self):
        for msg in self._message_history:
            yield msg
            
    def _metadata(self):
        return {'timestamp': time.strftime('%a %d %b %Y %H:%M'),
                'sources': self._heymans.documentation.to_json(),
                'search_model': config.search_model,
                'condense_model': config.condense_model,
                'answer_model': config.answer_model}
        
    def append(self, role, message):
        metadata = self._metadata()
        self._message_history.append((role, message, metadata))
        self._condensed_message_history.append((role, message))
        self._condense_message_history()
        if self._persistent:
            self._save()
        return metadata
    
    def prompt(self):
        prompt = [SystemMessage(content=self._system_prompt())]
        for role, content in self._condensed_message_history:
            if role == 'assistant':
                prompt.append(AIMessage(content=content))
            elif role == 'user':
                prompt.append(HumanMessage(content=content))
            else:
                raise ValueError(f'Invalid role: {role}')
        return prompt

    def welcome_message(self):
        return config.welcome_message
        
    def _condense_message_history(self):
        system_prompt = self._system_prompt()
        messages = [{"role": "system", "content": system_prompt}]
        prompt_length = sum(len(content) for role, content
                            in self._condensed_message_history)
        logger.info(f'system prompt length: {len(system_prompt)}')
        logger.info(f'prompt length (without system prompt): {prompt_length}')
        if prompt_length <= config.max_prompt_length:
            logger.info('no need to condense')
            return
        condense_length = 0
        condense_messages = []
        while condense_length < config.condense_chunk_length:
            role, content = self._condensed_message_history.pop(0)
            condense_length += len(content)
            condense_messages.insert(0, (role, content))
        logger.info(f'condensing {len(condense_messages)} messages')
        condense_prompt = prompt.render(
            prompt.CONDENSE_HISTORY,
            history=''.join(
                f'You said: {content}' if role == 'system' else f'User said: {content}'
                for role, content in condense_messages))
        self._condensed_text = self._heymans.condense_model.predict(
            condense_prompt)
        
    def _system_prompt(self):
        if len(self._heymans.documentation):
            logger.info('using system prompt with documentation')
            system_prompt = prompt.render(
                prompt.SYSTEM_PROMPT_WITH_DOC,
                documentation=str(self._heymans.documentation))
        else:
            logger.info('using system prompt without documentation')
            system_prompt = prompt.SYSTEM_PROMPT_NO_DOC
        if self._condensed_text:
            logger.info('appending condensed text to system prompt')
            system_prompt += prompt.render(
                prompt.SYSTEM_PROMPT_CONDENSED,
                summary=self._condensed_text)
        return system_prompt

    def _load(self):
        if not self._session_path.exists():
            logger.info(f'starting new session: {self._session_path}')
            return
        logger.info(f'loading session file: {self._session_path}')
        try:
            session = json.loads(self._session_path.read_text())
        except json.JSONDecodeError:
            logger.warning(f'failed to load session file: {self._session_path}')
            return
        if not isinstance(session, dict):
            logger.warning(f'session file invalid: {self._session_path}')
            return
        self._condensed_text = session.get('condensed_text', None)
        self._message_history = session.get('message_history', [])
        self._condensed_message_history = session.get(
            'condensed_message_history', [])
        self._message_metadata = session.get('message_metadata', [])
    
    def _save(self):
        session = {
            'condensed_text': self._condensed_text,
            'message_history': self._message_history,
            'condensed_message_history': self._condensed_message_history,
        }
        logger.info(f'saving session file: {self._session_path}')
        self._session_path.write_text(json.dumps(session))


def adler32(data):
    """Compute the Adler32 checksum of the file specified by `path`."""
    return zlib.adler32(data.encode('utf-8')) & 0xffffffff

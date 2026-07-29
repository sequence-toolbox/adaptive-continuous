"""Modified version for entanglement generation"""

from typing import Optional, TYPE_CHECKING, Any
from enum import Enum, auto

from sequence.topology.node import Node
from sequence.components.memory import Memory
from sequence.topology.node import BSMNode
from sequence.utils import log
from sequence.entanglement_management.entanglement_protocol import EntanglementProtocol
from sequence.entanglement_management.generation.generation_base import EntanglementGenerationA, EntanglementGenerationB
from sequence.entanglement_management.generation.barret_kok import BarretKokA, BarretKokB
from sequence.entanglement_management.generation.single_heralded import SingleHeraldedA, SingleHeraldedB
from sequence.message import Message
from sequence.kernel.event import Event
from sequence.kernel.process import Process
from sequence.resource_management.memory_manager import MemoryInfo, MemoryManager
from sequence.constants import SINGLE_HERALDED

if TYPE_CHECKING:
    from adaptive_continuous import AdaptiveContinuousProtocol
    from router_net_topo_adaptive import QuantumRouterAdaptive
    from sequence.components.bsm import SingleHeraldedBSM


# custom generation types
BARRET_KOK_ADAPTIVE = 'barret_kok_adaptive'
SINGLE_HERALDED_ADAPTIVE = 'single_heralded_adaptive'


def valid_trigger_time(trigger_time: int, target_time: int, resolution: int) -> bool:
    """return True if the trigger time is valid, else return False."""
    lower = target_time - (resolution // 2)
    upper = target_time + (resolution // 2)
    return lower <= trigger_time <= upper


class GenerationMsgType(Enum):
    """Defines possible message types for entanglement generation."""

    NEGOTIATE = auto()
    NEGOTIATE_ACK = auto()
    MEAS_RES = auto()
    INFORM_EP = auto()


class EntanglementGenerationMessage(Message):
    """Message used by entanglement generation protocols.

    This message contains all information passed between generation protocol instances.
    Messages of different types contain different information.

    Attributes:
        msg_type (GenerationMsgType): defines the message type.
        receiver (str): name of destination protocol instance.
        qc_delay (int): quantum channel delay to BSM node (if `msg_type == NEGOTIATE`).
        frequency (float): frequency with which local memory can be excited (if `msg_type == NEGOTIATE`).
        emit_time (int): time to emit photon for measurement (if `msg_type == NEGOTIATE_ACK`).
        detector (int): detector number at BSM node (if `msg_type == MEAS_RES`).
        time (int): detection time at BSM node (if `msg_type == MEAS_RES`).
        resolution (int): time resolution of BSM detectors (if `msg_type == MEAS_RES`).
    """

    def __init__(self, msg_type: GenerationMsgType, receiver: str, protocol_type: str, **kwargs):
        super().__init__(msg_type, receiver)

        self.protocol_type = protocol_type

        self.qc_delay: int | None = None
        self.frequency: float | None = None
        self.emit_time: int | None = None
        self.detector: int | None = None
        self.time: int | None = None
        self.resolution: int | None = None
        self.entanglement_pair: tuple | None = None

        fields = {
            GenerationMsgType.NEGOTIATE: ['qc_delay', 'frequency'],
            GenerationMsgType.NEGOTIATE_ACK: ['emit_time'],
            GenerationMsgType.MEAS_RES: ['detector', 'time', 'resolution'],
            GenerationMsgType.INFORM_EP: ['entanglement_pair']
        }

        if msg_type in fields:
            for field in fields[msg_type]:
                setattr(self, field, kwargs.get(field))
        else:
            raise ValueError(f'EntanglementGeneration generated invalid message type {msg_type}')

    def __repr__(self):
        match self.msg_type:
            case GenerationMsgType.NEGOTIATE:
                return f'type: {self.msg_type}, qc_delay: {self.qc_delay}, frequency: {self.frequency}'
            case GenerationMsgType.NEGOTIATE_ACK:
                return f'type: {self.msg_type}, emit_time: {self.emit_time}'
            case GenerationMsgType.MEAS_RES:
                return f'type: {self.msg_type}, detector: {self.detector}, time: {self.time}, resolution: {self.resolution}'
            case GenerationMsgType.INFORM_EP:
                return f'type: {self.msg_type}, entanglement_pair: {self.entanglement_pair}'
            case _:
                raise Exception(f'EntanglementGeneration generated invalid message type {self.msg_type}')


@EntanglementGenerationA.register(BARRET_KOK_ADAPTIVE)
class BarretKokAAdaptive(BarretKokA):
    """Entanglement generation protocol for quantum router.

    Customized for the Adaptive Continuous Protocol

    The EntanglementGenerationA protocol should be instantiated on a quantum router node.
    Instances will communicate with each other (and with the B instance on a BSM node) to generate entanglement.

    Attributes:
        owner (QuantumRouterAdaptive): node that protocol instance is attached to.
        name (str): label for protocol instance.
        middle (str): name of BSM measurement node where emitted photons should be directed.
        remote_node_name (str): name of distant QuantumRouter node, containing a memory to be entangled with local memory.
        memory (Memory): quantum memory object to attempt entanglement for.
        from_app_request (bool): if true, then the EG protocol is generated by a request from an app
                                 if False, then the EG protocol is generated by a AC protocol
    """

    def __init__(self, owner: QuantumRouterAdaptive, name: str, middle: str, other: str, memory: Memory, from_app_request: bool = True):
        """Constructor for entanglement generation A class.

        Args:
            owner (QuantumRouterAdaptive): node to attach protocol to.
            name (str): name of protocol instance.
            middle (str): name of middle measurement node.
            other (str): name of other node.
            memory (Memory): memory to entangle.
        """

        super().__init__(owner, name, middle, other, memory)
        self.protocol_type = BARRET_KOK_ADAPTIVE

        # miscellaneous other parameters
        self.from_app_request: bool = from_app_request
        self.node_send_resource_management_request: bool = False
        self.matched_entanglement_pair = None

    
    def start(self) -> None:
        """Method to start "one round" in the entanglement generation protocol (there are two rounds in Barrett-Kok).

        Will start negotiations with other protocol (if primary).

        Side Effects:
            Will send message through attached node.
        """

        log.logger.info(f"{self.name} protocol start with partner {self.remote_protocol_name}")

        # to avoid start after remove protocol
        if self not in self.owner.protocols:
            return

        # update memory, and if necessary, start negotiations for round
        if self.update_memory():

            if self.primary:
                if not self.from_app_request:   # EGA protocol is generated from the adaptive continuous protocol
                    log.logger.debug(f"{self.name} protocol adaptive start")
                    self.qc_delay = self.owner.qchannels[self.middle].delay          # send NEGOTIATE message as normal
                    frequency = self.memory.frequency
                    message = EntanglementGenerationMessage(GenerationMsgType.NEGOTIATE,
                                                            self.remote_protocol_name,
                                                            protocol_type=self.protocol_type,
                                                            qc_delay=self.qc_delay,
                                                            frequency=frequency)
                    self.owner.send_message(self.remote_node_name, message)

                # EGA protocol is generated from the request
                else:
                    if self.ent_round == 1:

                        # if not informed EP
                        if self.matched_entanglement_pair is None:
                            # first check if there is pre-generated entanglement pair
                            adaptive_continuous: AdaptiveContinuousProtocol = self.owner.adaptive_continuous
                            this_node_name = self.owner.name
                            remote_node_name = self.remote_node_name
                            matched_entanglement_pair = adaptive_continuous.match_generated_entanglement_pair(this_node_name, remote_node_name)

                            # no pre-generated entanglement pair
                            # send NEGOTIATE message as normal
                            if matched_entanglement_pair is None:
                                self.qc_delay = self.owner.qchannels[self.middle].delay
                                frequency = self.memory.frequency
                                message = EntanglementGenerationMessage(GenerationMsgType.NEGOTIATE,
                                                                        self.remote_protocol_name,
                                                                        protocol_type=self.protocol_type,
                                                                        qc_delay=self.qc_delay,
                                                                        frequency=frequency)
                                self.owner.send_message(self.remote_node_name, message)

                            # has pre-generated entanglement pair
                            else:
                                log.logger.info(f'{this_node_name} match pre-generated entanglement pair {matched_entanglement_pair}')
                                adaptive_continuous.remove_entanglement_pair(matched_entanglement_pair)
                                msg = EntanglementGenerationMessage(GenerationMsgType.INFORM_EP,
                                                                    self.remote_protocol_name,
                                                                    protocol_type=self.protocol_type,
                                                                    entanglement_pair=matched_entanglement_pair)
                                self.owner.send_message(self.remote_node_name, msg)

                                # swap the memory at a future time
                                entangled_memory_name = self.get_entanglement_memory_name(matched_entanglement_pair)
                                classical_delay = self.owner.cchannels[self.remote_node_name].delay
                                future_swap_time = self.owner.timeline.now() + classical_delay
                                occupied_memory_name = self.memory.name
                                process = Process(self, 'swap_two_memory', [occupied_memory_name, entangled_memory_name])
                                event = Event(future_swap_time, process)
                                self.owner.timeline.schedule(event)
                                self.scheduled_events.append(event)

                        # if informed EP
                        else:
                            adaptive_continuous = self.owner.adaptive_continuous
                            try:
                                entangled_memory_name = self.get_entanglement_memory_name(self.matched_entanglement_pair)
                                adaptive_continuous.remove_entanglement_pair(self.matched_entanglement_pair)
                                self.swap_two_memory(self.memory.name, entangled_memory_name)
                            except Exception as e:
                                log.logger.warning(f'{self.owner.name} Swap memory failed between {self.memory.name} and {entangled_memory_name}! Error message: {e}. ')
                                self.update_resource_manager(self.memory, MemoryInfo.RAW)

                    elif self.ent_round == 2:
                        self.qc_delay = self.owner.qchannels[self.middle].delay   # send NEGOTIATE message as normal
                        frequency = self.memory.frequency
                        message = EntanglementGenerationMessage(GenerationMsgType.NEGOTIATE,
                                                                self.remote_protocol_name,
                                                                protocol_type=self.protocol_type,
                                                                qc_delay=self.qc_delay,
                                                                frequency=frequency)
                        self.owner.send_message(self.remote_node_name, message)
                    
                    else:
                        pass

            # not primary
            else:
                # is from app request and not sent resource_management REQUEST
                if self.from_app_request and not self.node_send_resource_management_request:
                    # select EP and inform to other node
                    # first check if there is pre-generated entanglement pair
                    adaptive_continuous = self.owner.adaptive_continuous
                    this_node_name = self.owner.name
                    remote_node_name = self.remote_node_name
                    self.matched_entanglement_pair = adaptive_continuous.match_generated_entanglement_pair(this_node_name, remote_node_name)

                    # has pre-generated entanglement pair
                    if self.matched_entanglement_pair is not None:
                        log.logger.info(f'{this_node_name} match pre-generated entanglement pair {self.matched_entanglement_pair}')
                        adaptive_continuous.remove_entanglement_pair(self.matched_entanglement_pair)
                        msg = EntanglementGenerationMessage(GenerationMsgType.INFORM_EP,
                                                            self.remote_protocol_name,
                                                            protocol_type=self.protocol_type,
                                                            entanglement_pair=self.matched_entanglement_pair)
                        self.owner.send_message(self.remote_node_name, msg, priority=0)
                        # swap the memory at a future time
                        entangled_memory_name = self.get_entanglement_memory_name(self.matched_entanglement_pair)
                        classical_delay = self.owner.cchannels[self.remote_node_name].delay
                        future_swap_time = self.owner.timeline.now() + classical_delay
                        occupied_memory_name = self.memory.name
                        process = Process(self, 'swap_two_memory', [occupied_memory_name, entangled_memory_name])
                        event = Event(future_swap_time, process)
                        self.owner.timeline.schedule(event)
                        self.scheduled_events.append(event)
                    else:
                        pass
                else:
                    pass


    def get_entanglement_memory_name(self, entanglement_pair: tuple) -> str:
        """Given the entanglement_pair, return the entangled_memory to swap with self.memory

        Args:
            entanglement_pair: ((node_name, entangled_memory_name), (node_name, entangled_memory_name)), all names are str
        """

        for node_name, memory_name in entanglement_pair:
            if node_name == self.owner.name:
                entangled_memory_name = memory_name
                return entangled_memory_name
        else:
            raise Exception(f'{self.owner.name} not in {entanglement_pair}')


    def swap_two_memory(self, occupied_memory_name: str, entangled_memory_name: str):
        """Swap memory between self.memory and the memory that entangled_memory_name is referring to

        Args:
            occupied_memory_name: the name of the occupied memory (i.e., self.memory of the entanglement generation protocol)
            entangled_memory_name: the name of the entangled memory (generated by the adaptive continuous protocol) on this node
        """

        if not self.check_entangled_memory(entangled_memory_name):
            # Adaptive continuous protocol's reservation expire in the middle of swap_memory protocol
            log.logger.info(f'{self.owner.name} Swap memory failed between {occupied_memory_name} and {entangled_memory_name}!')
            self.update_resource_manager(self.memory, MemoryInfo.RAW)
            return

        if self not in self.owner.protocols:
            # Request's reservation expire in the middle of swap_memory protocol
            log.logger.info(f'{self.owner.name} Swap memory failed between {occupied_memory_name} and {entangled_memory_name}!')
            self.update_resource_manager(self.memory, MemoryInfo.RAW)
            return

        log.logger.info(f'{self.owner.name} Swap memory between {occupied_memory_name} and {entangled_memory_name}')
        self.owner.resource_manager.swap_two_memory(occupied_memory_name, entangled_memory_name) # the memory_array is updated, but more needs to update

        memory_manager = self.get_memory_manager()
        memory_array = memory_manager.memory_array
        # after swapping, the entangled memory turns into occupied memory, while the occupied memory turns into entangled memory
        entangled_memory = memory_array.get_memory_by_name(occupied_memory_name)
        occupied_memory  = memory_array.get_memory_by_name(entangled_memory_name)

        # update self.memory (the current entangled memory)
        self.memory = entangled_memory
        self.memories = [self.memory]
        self.memory.entangled_memory['memo_id'] = self.remote_memo_id
        mem_info = memory_manager.get_info_by_memory(entangled_memory)
        mem_info.remote_memo = self.remote_memo_id
        self.update_resource_manager_swap_memory(self, self.memory)

        # update the current occupied memory to RAW
        mem_info = memory_manager.get_info_by_memory(occupied_memory)
        mem_info.to_raw()
        self.update_resource_manager_swap_memory(None, occupied_memory)


    def check_entangled_memory(self, entangled_memory_name: str) -> bool:
        """Check if the parameter entangled_memory_name is indeed in an entangled state.

        Useful when the AC protocol's expire event interrupted the swapping protocol process.
        
        Args:
            entangled_memory_name: name of the memory that is in entangled state

        Return:
            True if the memory is indeed entangled, otherwise False
        """

        return self.owner.resource_manager.check_entangled_memory(entangled_memory_name)


    def update_resource_manager_swap_memory(self, protocol: Optional[EntanglementProtocol], memory: Memory):
        """Update the resource manager when using the swap memory, use resource_manager.update_swap_memory()"""

        self.owner.resource_manager.update_swap_memory(protocol, memory)


    def get_memory_manager(self) -> MemoryManager:
        """Get the memory manager that is associated to self.owner"""
        return self.owner.resource_manager.memory_manager


    def received_message(self, src: str, msg: EntanglementGenerationMessage) -> None:
        """Method to receive messages.

        This method receives messages from other entanglement generation protocols.
        Depending on the message, different actions may be taken by the protocol.

        Args:
            src (str): name of the source node sending the message.
            msg (EntanglementGenerationMessage): message received.

        Side Effects:
            May schedule various internal and hardware events.
        """

        if src not in [self.middle, self.remote_node_name]:
            return

        msg_type = msg.msg_type

        log.logger.debug("{} {} received message from node {} of type {}, round={}".format(
                         self.owner.name, self.name, src, msg.msg_type, self.ent_round))

        if msg_type is GenerationMsgType.NEGOTIATE:  # primary -> non-primary
            # configure params
            other_qc_delay = msg.qc_delay
            self.qc_delay = self.owner.qchannels[self.middle].delay
            cc_delay = int(self.owner.cchannels[src].delay)

            # get time for first excite event
            memory_excite_time = self.memory.next_excite_time
            min_time = max(self.owner.timeline.now(),
                           memory_excite_time) + other_qc_delay - self.qc_delay + cc_delay  # cc_delay time for NEGOTIATE_ACK
            emit_time = self.owner.schedule_qubit(self.middle, min_time)  # used to send memory
            self.expected_time = emit_time + self.qc_delay  # expected time for middle BSM node to receive the photon

            # schedule emit
            process = Process(self, "emit_event", [])
            event = Event(emit_time, process)
            self.owner.timeline.schedule(event)
            self.scheduled_events.append(event)

            # send negotiate_ack
            other_emit_time = emit_time + self.qc_delay - other_qc_delay
            message = EntanglementGenerationMessage(GenerationMsgType.NEGOTIATE_ACK,
                                                    self.remote_protocol_name,
                                                    protocol_type=self.protocol_type,
                                                    emit_time=other_emit_time)
            self.owner.send_message(src, message)

            # schedule start if necessary (current is first round, need second round),
            # else schedule update_memory (currently second round)
            # TODO: base future start time on resolution
            future_start_time = self.expected_time + self.owner.cchannels[
                self.middle].delay + 10  # delay is for sending the BSM_RES to end nodes, 10 is a small gap
            if self.ent_round == 1:
                process = Process(self, "start", [])  # for the second round
            else:
                process = Process(self, "update_memory", [])
            priority = self.owner.timeline.schedule_counter
            event = Event(future_start_time, process, priority)
            self.owner.timeline.schedule(event)
            self.scheduled_events.append(event)

        elif msg_type is GenerationMsgType.NEGOTIATE_ACK:  # non-primary --> primary
            # configure params
            self.expected_time = msg.emit_time + self.qc_delay  # expected time for middle BSM node to receive the photon

            if msg.emit_time < self.owner.timeline.now():  # emit time calculated by the non-primary node
                msg.emit_time = self.owner.timeline.now()

            # schedule emit
            emit_time = self.owner.schedule_qubit(self.middle, msg.emit_time)
            assert emit_time == msg.emit_time, f"Invalid eg emit times {emit_time} {msg.emit_time} {self.owner.timeline.now()}"

            process = Process(self, "emit_event", [])
            event = Event(msg.emit_time, process)
            self.owner.timeline.schedule(event)
            self.scheduled_events.append(event)

            # schedule start if necessary (current is first round, need second round),
            # else schedule update_memory (currently second round)
            # TODO: base future start time on resolution
            future_start_time = self.expected_time + self.owner.cchannels[self.middle].delay + 10
            if self.ent_round == 1:
                process = Process(self, "start", [])  # for the second round
            else:
                process = Process(self, "update_memory", [])
            priority = self.owner.timeline.schedule_counter
            event = Event(future_start_time, process, priority)
            self.owner.timeline.schedule(event)
            self.scheduled_events.append(event)

        elif msg_type is GenerationMsgType.MEAS_RES:  # from middle BSM to both non-primary and primary
            detector = msg.detector
            time = msg.time
            resolution = msg.resolution

            log.logger.debug("{} received MEAS_RES={} at time={:,}, expected={:,}, resolution={}, round={}".format(
                self.owner.name, detector, time, self.expected_time, resolution, self.ent_round))

            if valid_trigger_time(time, self.expected_time, resolution):
                # record result if we don't already have one
                i = self.ent_round - 1
                if self.bsm_res[i] == -1:
                    self.bsm_res[i] = detector  # save the measurement results (detector number)
                else:
                    self.bsm_res[i] = -1  # BSM measured 1, 1 (both photons kept)
            else:
                log.logger.debug(f'{self.owner.name} BSM trigger time not valid')


        elif msg_type is GenerationMsgType.INFORM_EP:

            if self.remote_protocol_name is None:                       # protocol not paired with remote
                self.matched_entanglement_pair = msg.entanglement_pair  # save msg.entanglement_pair
            else:                                                       # already paired with remote
                adaptive_continuous = self.owner.adaptive_continuous
                try:
                    entangled_memory_name = self.get_entanglement_memory_name(msg.entanglement_pair)
                    adaptive_continuous.remove_entanglement_pair(msg.entanglement_pair)
                    self.swap_two_memory(self.memory.name, entangled_memory_name)
                except Exception as e:
                    log.logger.warning(f'{self.owner.name} Swap memory failed between {self.memory.name} and {entangled_memory_name}! Error message: {e}. ')
                    self.update_resource_manager(self.memory, MemoryInfo.RAW)

        else:
            raise Exception("Invalid message {} received by EG on node {}".format(msg_type, self.owner.name))


    def memory_expire(self, memory: "Memory") -> None:
        """Method to receive expired memories."""

        assert memory == self.memory

        self.update_resource_manager(memory, MemoryInfo.RAW)
        for event in self.scheduled_events:
            if event.time >= self.owner.timeline.now():
                self.owner.timeline.remove_event(event)


@EntanglementGenerationB.register(BARRET_KOK_ADAPTIVE)
class BarretKokBAdaptive(BarretKokB):
    """Entanglement generation protocol for BSM node.

    The EntanglementGenerationB protocol should be instantiated on a BSM node.
    Instances will communicate with the A instance on neighboring quantum router nodes to generate entanglement.

    Attributes:
        owner (BSMNode): node that protocol instance is attached to.
        name (str): label for protocol instance.
        others (List[str]): list of neighboring quantum router nodes
    """

    def __init__(self, owner: "BSMNode", name: str, others: list[str]):
        """Constructor for entanglement generation B protocol.

        Args:
            owner (Node): attached node.
            name (str): name of protocol instance.
            others (List[str]): name of protocol instance on end nodes.
        """
        super().__init__(owner, name, others)
        self.protocol_type = BARRET_KOK_ADAPTIVE


@EntanglementGenerationA.register('single_heralded_adaptive')
class SingleHeraldedAAdaptive(SingleHeraldedA):
    """Single heralded entanglement generation protocol for quantum router.

    Uses Bell diagonal state and compute fidelity analytically

    Customized for the Adaptive Continuous Protocol
    
    The SingleHeraldedAAdaptive protocol should be instantiated on a quantum router node.
    Instances will communicate with each other (and with the B instance on a BSM node) to generate entanglement.

    Attributes:
        owner (QuantumRouterAdaptive): node that protocol instance is attached to.
        name (str): label for protocol instance.
        middle (str): name of BSM measurement node where emitted photons should be directed.
        remote_node_name (str): name of distant QuantumRouter node, containing a memory to be entangled with local memory.
        memory (Memory): quantum memory object to attempt entanglement for.
        from_app_request (bool): if true, then the EG protocol is generated by a request from an app
                                 if False, then the EG protocol is generated by a AC protocol
        raw_epr_errors (list): assuming BDS form of raw EPR pair, probability distribution of X, Y, Z Pauli errors
    """

    def __init__(self, owner: QuantumRouterAdaptive, name: str, middle: str, other: str, memory: Memory, from_app_request: bool = True, raw_epr_errors: list[float] = None):
        """Constructor for entanglement generation A class.

        Args:
            owner (Node): node to attach protocol to.
            name (str): name of protocol instance.
            middle (str): name of middle measurement node.
            other (str): name of other node.
            memory (Memory): memory to entangle.
            from_app_request (bool): if true, then the EG protocol is generated by a request from an app
                                     if False, then the EG protocol is generated by a AC protocol
            raw_epr_errors (list): assuming BDS form of raw EPR pair, probability distribution of X, Y, Z Pauli errors
        """

        super().__init__(owner, name, middle, other, memory, raw_epr_errors=raw_epr_errors)
        self.protocol_type = SINGLE_HERALDED_ADAPTIVE

        # misc
        self.from_app_request: bool = from_app_request
        self.node_send_resource_management_request: bool = False
        self.select_ep = False    # this node select the EP for memory assignment
        self.matched_entanglement_pair = None


    def start(self) -> None:
        """Method to start "one round" in the entanglement generation protocol (there are two rounds in Barrett-Kok).

        Will start negotiations with other protocol (if primary).

        Side Effects:
            Will send message through attached node.
        """

        log.logger.info(f"{self.name} protocol start with partner {self.remote_protocol_name}")

        # to avoid start after remove protocol
        if self not in self.owner.protocols:
            return

        # update memory, and if necessary start negotiations for round
        if self.update_memory() and self.primary:

            if not self.from_app_request:   # EGA protocol is generated from the adaptive continuous protocol
                log.logger.debug(f"{self.name} protocol adaptive start")
                self.qc_delay = self.owner.qchannels[self.middle].delay          # send NEGOTIATE message as normal
                frequency = self.memory.frequency
                message = EntanglementGenerationMessage(GenerationMsgType.NEGOTIATE,
                                                        self.remote_protocol_name,
                                                        protocol_type=self.protocol_type,
                                                        qc_delay=self.qc_delay,
                                                        frequency=frequency)
                self.owner.send_message(self.remote_node_name, message)

            else:                                # EGA protocol is generated from the request
                adaptive_continuous: AdaptiveContinuousProtocol = self.owner.adaptive_continuous         # first check if there is pre-generated entanglement pair
                this_node_name = self.owner.name
                remote_node_name = self.remote_node_name
                matched_entanglement_pair = adaptive_continuous.match_generated_entanglement_pair(this_node_name, remote_node_name)

                if matched_entanglement_pair is None:                        # no pre-generated entanglement pair
                    log.logger.debug(f"{self.name} protocol no pre-generated entanglement pair")
                    self.qc_delay = self.owner.qchannels[self.middle].delay  # send NEGOTIATE message as normal
                    frequency = self.memory.frequency
                    message = EntanglementGenerationMessage(GenerationMsgType.NEGOTIATE,
                                                            self.remote_protocol_name,
                                                            protocol_type=self.protocol_type,
                                                            qc_delay=self.qc_delay,
                                                            frequency=frequency)
                    self.owner.send_message(self.remote_node_name, message)

                else:                                                        # has pre-generated entanglement pair
                    log.logger.info(f'{this_node_name} match pre-generated entanglement pair {matched_entanglement_pair}')
                    adaptive_continuous.remove_entanglement_pair(matched_entanglement_pair)
                    msg = EntanglementGenerationMessage(GenerationMsgType.INFORM_EP,
                                                        self.remote_protocol_name,
                                                        protocol_type=self.protocol_type,
                                                        entanglement_pair=matched_entanglement_pair,)
                    self.owner.send_message(self.remote_node_name, msg)
                    # swap the memory at a future time
                    entangled_memory_name = self.get_entanglement_memory_name(matched_entanglement_pair)
                    classical_delay = self.owner.cchannels[self.remote_node_name].delay
                    future_swap_time = self.owner.timeline.now() + classical_delay
                    occupied_memory_name = self.memory.name
                    process = Process(self, 'swap_two_memory', [occupied_memory_name, entangled_memory_name])
                    event = Event(future_swap_time, process)
                    self.owner.timeline.schedule(event)
                    self.scheduled_events.append(event)


    def get_entanglement_memory_name(self, entanglement_pair: tuple) -> str:
        """Given the entanglement_pair, return the entangled_memory to swap with self.memory

        Args:
            entanglement_pair: ((node_name, entangled_memory_name), (node_name, entangled_memory_name)), all names are str
        """

        for node_name, memory_name in entanglement_pair:
            if node_name == self.owner.name:
                entangled_memory_name = memory_name
                return entangled_memory_name
        else:
            raise Exception(f'{self.owner.name} not in {entanglement_pair}')


    def swap_two_memory(self, occupied_memory_name: str, entangled_memory_name: str):
        """Swap memory between self.memory and the memory that entangled_memory_name is referring to

        Args:
            occupied_memory_name:  the name of the occupied memory (i.e., self.memory of the entanglement generation protocol)
            entangled_memory_name: the name of the entangled memory (generated by the adaptive continuous protocol) on this node
        """
        if not self.check_entangled_memory(entangled_memory_name):
            # Adaptive continuous protocol's reservation expire in the middle of swap_memory protocol
            log.logger.warning(f'{self.owner.name} Swap memory failed between {occupied_memory_name} and {entangled_memory_name}!')
            self.update_resource_manager(self.memory, MemoryInfo.RAW)
            return

        if self not in self.owner.protocols:
            # Request's reservation expire in the middle of swap_memory protocol
            log.logger.warning(f'{self.owner.name} Swap memory failed between {occupied_memory_name} and {entangled_memory_name}!')
            self.update_resource_manager(self.memory, MemoryInfo.RAW)
            return

        log.logger.info(f'{self.owner.name} Swap memory between {occupied_memory_name} and {entangled_memory_name}')
        memory_manager = self.get_memory_manager()
        memory_array = memory_manager.memory_array

        # udpate the memory fidelity here. Note: for two entangled memories at two nodes,
        # only the node that first run the code will success, the node that runs second will fail,
        # because in the second run, `other_memory` is already swapped at the remote node, but not reflected here
        try:
            entangled_memory: Memory = memory_array.get_memory_by_name(entangled_memory_name)
            mem_info: MemoryInfo = memory_manager.get_info_by_memory(entangled_memory)
            other_memory: Memory = self.owner.timeline.get_entity_by_name(mem_info.remote_memo)
            entangled_memory.bds_decohere()
            other_memory.bds_decohere()
        except Exception as e:
            log.logger.error(f'{self.name}: key error {e}')
        finally:
            mem_info.fidelity = entangled_memory.fidelity = entangled_memory.get_bds_fidelity()

        # the swapping of attributes in memory and mem_info object
        self.owner.resource_manager.swap_two_memory(occupied_memory_name, entangled_memory_name) # the memory_array is updated, but more needs to update

        # update self.memory (the current entangled memory)
        self.memory.entangled_memory['memo_id'] = self.remote_memo_id
        mem_info = memory_manager.get_info_by_memory(self.memory)
        mem_info.remote_memo = self.remote_memo_id
        self.update_resource_manager_swap_memory(self, self.memory)

        # update the current occupied memory to RAW
        # after swapping, the entangled memory turns into occupied memory, while the occupied memory turns into entangled memory
        occupied_memory  = memory_array.get_memory_by_name(entangled_memory_name)
        mem_info = memory_manager.get_info_by_memory(occupied_memory)
        mem_info.to_raw()
        self.update_resource_manager_swap_memory(None, occupied_memory)


    def check_entangled_memory(self, entangled_memory_name: str) -> bool:
        """check if the parameter entangled_memory_name is indeed in an entangled state
           useful when the AC protocol's expire event interrupted the swapping protocol process
        
        Args:
            entangled_memory_name: name of the memory that is in entangled state
        Return:
            True if the memory is indeed entangled, otherwise False
        """
        return self.owner.resource_manager.check_entangled_memory(entangled_memory_name)


    def update_resource_manager_swap_memory(self, protocol: Optional[EntanglementProtocol], memory: Memory):
        """Update the resource manager when using the swap memory, use resource_manager.update_swap_memory()"""
        self.owner.resource_manager.update_swap_memory(protocol, memory)


    def get_memory_manager(self) -> MemoryManager:
        """Get the memory manager that is associated to self.owner"""
        return self.owner.resource_manager.memory_manager


    def received_message(self, src: str, msg: EntanglementGenerationMessage) -> None:
        """Method to receive messages.

        This method receives messages from other entanglement generation protocols.
        Depending on the message, different actions may be taken by the protocol.

        Args:
            src (str): name of the source node sending the message.
            msg (EntanglementGenerationMessage): message received.

        Side Effects:
            May schedule various internal and hardware events.
        """

        if src not in [self.middle, self.remote_node_name]:
            return

        msg_type = msg.msg_type

        log.logger.debug("{} {} received message from node {} of type {}, round={}".format(self.owner.name, self.name, src, msg.msg_type, self.ent_round))

        if msg_type is GenerationMsgType.NEGOTIATE:  # primary -> non-primary
            # configure params
            other_qc_delay = msg.qc_delay
            self.qc_delay = self.owner.qchannels[self.middle].delay
            cc_delay = int(self.owner.cchannels[src].delay)

            # get time for first excite event
            memory_excite_time = self.memory.next_excite_time
            min_time = max(self.owner.timeline.now(),
                           memory_excite_time) + other_qc_delay - self.qc_delay + cc_delay  # cc_delay time for NEGOTIATE_ACK
            emit_time = self.owner.schedule_qubit(self.middle, min_time)  # used to send memory
            self.expected_time = emit_time + self.qc_delay  # expected time for middle BSM node to receive the photon

            # schedule emit
            process = Process(self, "emit_event", [])
            event = Event(emit_time, process)
            self.owner.timeline.schedule(event)
            self.scheduled_events.append(event)

            # send negotiate_ack
            other_emit_time = emit_time + self.qc_delay - other_qc_delay
            message = EntanglementGenerationMessage(GenerationMsgType.NEGOTIATE_ACK, self.remote_protocol_name,
                                                    self.protocol_type, emit_time=other_emit_time)
            self.owner.send_message(src, message)

            # schedule start if necessary (current is first round, need second round), else schedule update_memory (currently second round)
            # TODO: base future start time on resolution
            future_start_time = self.expected_time + self.owner.cchannels[
                self.middle].delay + 10  # delay is for sending the BSM_RES to end nodes, 10 is a small gap
            if self.ent_round == 1:
                process = Process(self, "start", [])  # for the second round
            else:
                process = Process(self, "update_memory", [])
            priority = self.owner.timeline.schedule_counter
            event = Event(future_start_time, process, priority)
            self.owner.timeline.schedule(event)
            self.scheduled_events.append(event)

        elif msg_type is GenerationMsgType.NEGOTIATE_ACK:  # non-primary --> primary
            # configure params
            self.expected_time = msg.emit_time + self.qc_delay  # expected time for middle BSM node to receive photon

            if msg.emit_time < self.owner.timeline.now():  # emit time calculated by the non-primary node
                msg.emit_time = self.owner.timeline.now()

            # schedule emit
            emit_time = self.owner.schedule_qubit(self.middle, msg.emit_time)
            assert emit_time == msg.emit_time, f"Invalid eg emit times {emit_time} {msg.emit_time} {self.owner.timeline.now()}"

            process = Process(self, "emit_event", [])
            event = Event(msg.emit_time, process)
            self.owner.timeline.schedule(event)
            self.scheduled_events.append(event)

            # schedule start if necessary (current is first round, need second round),
            # else schedule update_memory (currently second round)
            # TODO: base future start time on resolution
            future_start_time = self.expected_time + self.owner.cchannels[self.middle].delay + 10
            if self.ent_round == 1:
                process = Process(self, "start", [])  # for the second round
            else:
                process = Process(self, "update_memory", [])
            priority = self.owner.timeline.schedule_counter
            event = Event(future_start_time, process, priority)
            self.owner.timeline.schedule(event)
            self.scheduled_events.append(event)

        elif msg_type is GenerationMsgType.MEAS_RES:  # from middle BSM to both non-primary and primary
            detector = msg.detector
            time = msg.time
            resolution = msg.resolution

            log.logger.debug("{} received MEAS_RES={} at time={:,}, expected={:,}, resolution={}, round={}".format(
                self.owner.name, detector, time, self.expected_time, resolution, self.ent_round))

            if valid_trigger_time(time, self.expected_time, resolution):
                self.bsm_res[detector] += 1
            else:
                log.logger.debug(f'{self.owner.name} BSM trigger time not valid')

        elif msg_type is GenerationMsgType.INFORM_EP:  # primary --> non-primary
            self.matched_entanglement_pair = msg.entanglement_pair
            adaptive_continuous = self.owner.adaptive_continuous
            try:
                entangled_memory_name = self.get_entanglement_memory_name(msg.entanglement_pair)
                adaptive_continuous.remove_entanglement_pair(msg.entanglement_pair)
                self.swap_two_memory(self.memory.name, entangled_memory_name)
            except Exception as e:
                log.logger.warning(f'{self.owner.name} Swap memory failed between {self.memory.name} and {entangled_memory_name}! Error message: {e}. ')
                self.update_resource_manager(self.memory, MemoryInfo.RAW)

        else:
            raise Exception("Invalid message {} received by EG on node {}".format(msg_type, self.owner.name))


@EntanglementGenerationB.register('single_heralded_adaptive')
class SingleHeraldedBAdaptive(SingleHeraldedB):
    """Single heralded entanglement generation protocol for BSM node.

    The SingleHeraldedBAdaptive protocol should be instantiated on a BSM node.
    Instances will communicate with the A instance on neighboring quantum router nodes to generate entanglement.

    Attributes:
        owner (BSMNode): node that protocol instance is attached to.
        name (str): label for protocol instance.
        others (List[str]): list of neighboring quantum router nodes
    """

    def __init__(self, owner: BSMNode, name: str, others: list[str]):
        """Constructor for entanglement generation B protocol.

        Args:
            owner (BSMNode): attached node.
            name (str): name of protocol instance.
            others (List[str]): name of protocol instance on end nodes.
        """

        super().__init__(owner, name, others)
        self.protocol_type = SINGLE_HERALDED_ADAPTIVE

    def bsm_update(self, bsm: SingleHeraldedBSM, info: dict[str, Any]) -> None:
        """Method to receive detection events from BSM on node.

        Args:
            bsm (SingleAtomBSM or SingleHeraldedBSM): bsm object calling method.
            info (dict[str, any]): information passed from bsm.
        """
        assert bsm.encoding == SINGLE_HERALDED, "SingleHeraldedB should only be used with SingleHeraldedBSM."
        assert info['info_type'] == 'BSM_res'

        res = info['res']
        time = info['time']
        resolution = bsm.resolution

        for node in self.others:
            message = EntanglementGenerationMessage(GenerationMsgType.MEAS_RES, None, self.protocol_type,
                                                    detector=res, time=time, resolution=resolution)
            self.owner.send_message(node, message)

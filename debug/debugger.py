#! /bin/python
import gdb
import gdb.unwinder

#TODO this should be delayed until program code

class B9FrameId:
    def __init__(self, sp, pc, special = None):
        self.sp = sp
        self.pc = pc

        if special is not None:
            self.special = gdb.Value(special).cast(gdb.lookup_type("void").pointer())
            #self.special = special


class UnwindState:
    def __init__(self, pending_frame):
        self.in_interp = False
        self.executionContext = gdb.lookup_global_symbol("b9::currentExecutionContext").value()
        #self.saved_frame = pending_frame
        self.saved_ip = pending_frame.read_register("rip")
        self.saved_sp = pending_frame.read_register("rsp")
        self.next_frame = None
        self.state = 0
        self.bp_map = {}
        self.ctr = 0
        self.stack_base = self.executionContext['stack_']['stack_']

    # add a bp:sp mapping
    def add_frame(self, sp, bp):
        self.bp_map[int(sp)] = int(bp)

    def handle_initial_frame(self, pending_frame):
        print("initial unwind")
        #TODO: need to handle the case where there is only a single
        # interpreter frame
        real_pc = pending_frame.read_register("rip")
        pc = self.executionContext["ip_"]
        bp = self.executionContext["bp_"]
        sp = self.executionContext["stack_"]["top_"]
        old_pc = bp[-2]
        self.next_sp = bp
        
        real_sp = pending_frame.read_register("rsp")
        real_bp = pending_frame.read_register("rbp")
        print("\n\nPC = "+ str(pc) + "\n\n")


        frame_id = B9FrameId(real_sp, pc)
        unwind_info = pending_frame.create_unwind_info(frame_id)
        unwind_info.add_saved_register("rip", pc)
        unwind_info.add_saved_register("rsp", real_sp)
        unwind_info.add_saved_register("rbp", real_bp)
        self.add_frame(sp, bp)
        self.state = 1
        self.next_frame = bp
        return unwind_info
    
    def dummy_frame(self, pending_frame):
        bogus_sp = pending_frame.read_register("rsp")
        bogus_pc = pending_frame.read_register("rip")
        frame_id = B9FrameId(bogus_sp, bogus_pc)
        unwind_info = pending_frame.create_unwind_info(frame_id)
        unwind_info.add_saved_register("rip", bogus_pc)
        unwind_info.add_saved_register("rsp", bogus_sp)


    def unwind(self, pending_frame):
        print("\n\nUNWIND\n\n")
        if self.state == 0:
            return self.handle_initial_frame(pending_frame)
        #if self.state == 1:
        #    return self.dummy_frame(pending_frame)
        return None
        #TODO this is bogus, just die

        #sp = pending_frame.read_register("rsp")
        sp = self.next_frame

        stack = gdb.Value(sp).cast(gdb.lookup_type("OMR::Om::Value").pointer())

        """  stack_.push({Om::AS_UINT48, fn_});
        stack_.push({Om::AS_PTR, ip_});
        stack_.push({Om::AS_PTR, bp_});
        stack_.push({Om::AS_UINT48, std::uint64_t(type)});
        """
        
        pc = pending_frame.read_register("rip")

        payload_mask = gdb.lookup_global_symbol("OMR::Om::Value::PAYLOAD_MASK").value()
        #TODO need to check types on all this 
        #call_type = stack[-1]

        bp = int(stack[-2]['data_']['asRawValue'])
        if(bp == self.stack_base):
            return self.handle_end_frame()
        #bp_tag = 
        old_pc = int(stack[-3]['data_']['asRawValue'])
        if bp == self.stack_base:
            bp = self.saved_sp
            old_pc = self.saved_ip
        #fn = stack[-4]
        frame_id = B9FrameId(bogus_sp, bogus_pc, bp)
        self.add_frame(sp, bp)

        unwind_info = pending_frame.create_unwind_info(frame_id)
        unwind_info.add_saved_register("rip", bogus_pc)
        unwind_info.add_saved_register("rsp", bogus_sp)

        



class B9FrameUnwidner(gdb.unwinder.Unwinder):
    def __init__(self):
        super().__init__("B9FrameUnwinder")
        self.state = None
        gdb.events.cont.connect(self.invalidate_state)
        self.count = 0
        self.interp_block = None
        self.saved_frame = None


    def is_interpteter(self, pc):
        return pc >= self.interp_block.start and  pc <= self.interp_block.end
    
    def __call__(self, pending_frame):
        if self.state is not None:
            unwind_info = self.state.unwind(pending_frame)
            #if unwind_info is None:
            #    self.state = None
            return unwind_info
        
        if self.interp_block is None or not self.interp_block.is_valid():
            interp = gdb.lookup_global_symbol("b9::ExecutionContext::interpret")
            if interp is None:
                return None
            interp_addr = interp.value().cast(gdb.lookup_type("void").pointer())
            self.interp_block = gdb.block_for_pc(int(interp_addr))

        pc = pending_frame.read_register("rip")
        if self.is_interpteter(pc):
            self.state = UnwindState(pending_frame)
            return self.state.unwind(pending_frame)

        return None
    
    def invalidate_state(self, *args, **kwargs):
        self.state = None


my_unwinder = B9FrameUnwidner()
gdb.unwinder.register_unwinder(None, my_unwinder, True)

class B9Decorator:
    def __init__(self, frame):
        self.frame = frame
    
    def function(self):
        return "B9 interpreted function"

class B9FrameFilter:
    def __init__(self):
        self.name = "B9FrameFilter"
        self.priority = 100
        self.enabled = True
        gdb.frame_filters[self.name] = self

    def maybe_decorate_frame(self, frame):
        if my_unwinder is None or my_unwinder.state is None:
            return frame
        base = frame.inferior_frame()
        sp = int(base.read_register('rsp'))
        if sp in my_unwinder.state.bp_map:
            return B9Decorator(frame)
        return frame

        

    def filter(self, frame_iter):
        return map(self.maybe_decorate_frame, frame_iter)
    
class B9StackFrame:
    def __init__(self):
        self.pc = -1
        self.bp = -1
        self.type = "INVALID"

    
    # TODO this is awful, need to support selected frame etc
    @staticmethod
    def get_current_frame():
        #TODO we should be checking if we are actially in the interpreter
        (vm_run,dummy) = gdb.lookup_symbol("b9::VirtualMachine::run")
        if not vm_run or not vm_run.is_function:
            raise Exception()
        vm_run_addr = vm_run.value().cast(gdb.lookup_type("void").pointer())
        block = gdb.block_for_pc(int(vm_run_addr))

        exeContext = gdb.parse_and_eval("executionContext") #TODO this seems like a crappy way of doing things

        frame = B9StackFrame()
        #frame.pc = 


class TaggedValue:
    pass
"""
class B9Backtrace(gdb.command):
    def __init__(self):
        gdb.command.__init__(self, "b9-bt", gdb.COMMAND_STACK, gdb.COMPLETE_NONE)

    def invoke(self, args, from_tty):
        pass
   """ 
#B9Backtrace()